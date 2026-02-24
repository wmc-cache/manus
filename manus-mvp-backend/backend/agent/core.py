"""Agent 核心引擎 - 实现 Agent Loop"""
import asyncio
import json
import os
import logging
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional

from agent.context_manager import build_default_context_manager, ToolStateMachine
from agent.planner import Planner
from agent.tools import execute_tool
from llm.deepseek import chat_completion, chat_completion_stream
from models.schemas import (
    SSEEventType,
    Conversation,
    Message,
    MessageRole,
    ToolCall,
    ToolCallStatus,
    TaskPlan,
    PlanPhaseStatus,
)
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_workspace_root


logger = logging.getLogger(__name__)


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _read_positive_float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
        return value if value > 0 else default
    except ValueError:
        return default


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    if raw in {"1", "true", "yes", "on"}:
        return True
    if raw in {"0", "false", "no", "off"}:
        return False
    return default


MAX_ITERATIONS = _read_positive_int_env("MANUS_MAX_ITERATIONS", 30)
PROGRESS_HEARTBEAT_SECONDS = _read_positive_float_env("MANUS_PROGRESS_HEARTBEAT_SECONDS", 2.0)
TOOL_LOOP_WINDOW = _read_positive_int_env("MANUS_TOOL_LOOP_WINDOW", 8)
TOOL_LOOP_REPEAT_THRESHOLD = _read_positive_int_env("MANUS_TOOL_LOOP_REPEAT_THRESHOLD", 3)
PLAN_USE_LLM = _read_bool_env("MANUS_PLAN_USE_LLM", True)
DEFAULT_CONVERSATION_STORE = "/tmp/manus_workspace/conversations.json"
MAX_CONTEXT_MESSAGES = int(os.environ.get("MANUS_MAX_CONTEXT_MESSAGES", "40"))
MAX_RECENT_MESSAGE_CHARS = int(os.environ.get("MANUS_MAX_RECENT_MESSAGE_CHARS", "4000"))
MAX_OLD_MESSAGE_CHARS = int(os.environ.get("MANUS_MAX_OLD_MESSAGE_CHARS", "1200"))
TODO_FILENAME = "todo.md"
CONTINUE_MESSAGES = {"继续", "继续。", "continue", "continue.", "go on"}
DEFAULT_TOOL_NAMES = [
    "web_search",
    "wide_research",
    "spawn_sub_agents",
    "shell_exec",
    "execute_code",
    "browser_navigate",
    "browser_screenshot",
    "browser_get_content",
    "browser_click",
    "browser_input",
    "browser_scroll",
    "read_file",
    "write_file",
    "edit_file",
    "append_file",
    "list_files",
    "data_analysis",
]


class AgentEngine:
    """Agent 核心引擎"""

    def __init__(self):
        self.conversations: Dict[str, Conversation] = {}
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self._store_path = Path(os.environ.get("MANUS_CONVERSATIONS_FILE", DEFAULT_CONVERSATION_STORE))
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._save_fail_streak = 0
        self._context_manager = build_default_context_manager(
            max_context_messages=MAX_CONTEXT_MESSAGES,
            max_recent_message_chars=MAX_RECENT_MESSAGE_CHARS,
            max_old_message_chars=MAX_OLD_MESSAGE_CHARS,
        )
        self._planner = Planner(llm_func=chat_completion)
        self._plan_use_llm = PLAN_USE_LLM
        self._tool_state_machine = ToolStateMachine()
        self._load_conversations()

    def get_conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        """获取会话级串行锁，避免同一会话并发运行导致状态互相覆盖。"""
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    def _load_conversations(self):
        """从磁盘加载历史会话"""
        if not self._store_path.exists():
            return

        try:
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
            items = payload.get("conversations", [])
            loaded: Dict[str, Conversation] = {}
            for raw in items:
                conv = Conversation.model_validate(raw)
                loaded[conv.id] = conv
            self.conversations = loaded
        except Exception:
            # 文件损坏或格式不兼容时，不阻塞服务启动
            self.conversations = {}

    def _save_conversations(self):
        """将会话持久化到磁盘"""
        try:
            data = {
                "conversations": [
                    conv.model_dump(mode="json")
                    for conv in self.conversations.values()
                ]
            }
            tmp_path = self._store_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_path.replace(self._store_path)
            if self._save_fail_streak > 0:
                logger.info(
                    "Conversation persistence recovered after %d failures.",
                    self._save_fail_streak,
                )
            self._save_fail_streak = 0
        except Exception as exc:
            # 不因持久化异常中断主流程，但要记录并告警
            self._save_fail_streak += 1
            logger.warning(
                "Failed to persist conversations to %s (streak=%d): %s",
                self._store_path,
                self._save_fail_streak,
                exc,
            )
            if self._save_fail_streak >= 3:
                logger.error(
                    "ALERT: conversation persistence failed %d consecutive times.",
                    self._save_fail_streak,
                )

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Conversation:
        """获取或创建对话"""
        if conversation_id and conversation_id in self.conversations:
            return self.conversations[conversation_id]
        conv = Conversation()
        self.conversations[conv.id] = conv
        self._save_conversations()
        return conv

    async def delete_conversation(self, conversation_id: str) -> tuple[bool, str]:
        """删除会话。若会话正在执行，返回 busy。"""
        if conversation_id not in self.conversations:
            return False, "not_found"

        lock = self.get_conversation_lock(conversation_id)
        if lock.locked():
            return False, "busy"

        async with lock:
            if conversation_id not in self.conversations:
                return False, "not_found"
            del self.conversations[conversation_id]
            self._save_conversations()

        return True, "deleted"

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [内容已截断，省略 {len(text) - max_chars} 字符]"

    def _serialize_plan(self, plan: TaskPlan) -> Dict[str, Any]:
        return {
            "goal": plan.goal,
            "current_phase_id": plan.current_phase_id,
            "phases": [
                {
                    "id": phase.id,
                    "title": phase.title,
                    "status": phase.status.value,
                }
                for phase in plan.phases
            ],
        }

    def _plan_to_markdown(self, plan: TaskPlan) -> str:
        lines = [
            "# Todo",
            "",
            f"目标: {plan.goal}",
            "",
        ]

        status_marker = {
            PlanPhaseStatus.PENDING: "[ ]",
            PlanPhaseStatus.RUNNING: "[-]",
            PlanPhaseStatus.COMPLETED: "[x]",
            PlanPhaseStatus.FAILED: "[!]",
        }

        for phase in plan.phases:
            marker = status_marker.get(phase.status, "[ ]")
            lines.append(f"- {marker} {phase.id}. {phase.title} ({phase.status.value})")

        if plan.current_phase_id is not None:
            lines.append("")
            lines.append(f"当前阶段: {plan.current_phase_id}")

        return "\n".join(lines)

    def _persist_todo(self, conversation: Conversation) -> str:
        if not conversation.plan:
            return ""
        try:
            root = get_workspace_root(conversation.id)
            todo_path = Path(root) / TODO_FILENAME
            todo_path.write_text(self._plan_to_markdown(conversation.plan), encoding="utf-8")
            return str(todo_path)
        except Exception:
            return ""

    def _build_plan_update_payload(self, conversation: Conversation, reason: str) -> Dict[str, Any]:
        payload: Dict[str, Any] = {"reason": reason}
        if conversation.plan:
            payload["plan"] = self._serialize_plan(conversation.plan)
            todo_path = self._persist_todo(conversation)
            if todo_path:
                payload["todo_path"] = todo_path
        return payload

    async def _create_plan_for_turn(self, user_message: str) -> TaskPlan:
        try:
            return await self._planner.create_plan(
                user_message=user_message,
                use_llm=self._plan_use_llm,
            )
        except Exception as exc:
            logger.warning("Plan creation failed, fallback to template: %s", exc)
            return self._planner.create_template_plan(user_message)

    @staticmethod
    def _is_continue_message(user_message: str) -> bool:
        return user_message.strip().lower() in CONTINUE_MESSAGES

    async def _ensure_plan_for_turn(self, conversation: Conversation, user_message: str) -> str:
        if conversation.plan is None or not self._is_continue_message(user_message):
            conversation.plan = await self._create_plan_for_turn(user_message)
            return "initialized"

        plan = conversation.plan
        if not plan.phases:
            conversation.plan = await self._create_plan_for_turn(user_message)
            return "initialized"

        # 若无运行阶段，则继续当前未完成阶段
        running_phase = next((p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING), None)
        if running_phase is None:
            pending = next((p for p in plan.phases if p.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}), None)
            if pending:
                pending.status = PlanPhaseStatus.RUNNING
                plan.current_phase_id = pending.id
            else:
                # 已全部完成时，继续会话则重开最后阶段
                last_phase = plan.phases[-1]
                last_phase.status = PlanPhaseStatus.RUNNING
                plan.current_phase_id = last_phase.id
        elif plan.current_phase_id != running_phase.id:
            plan.current_phase_id = running_phase.id
        return "resumed"

    @staticmethod
    def _transition_plan_to_execution(plan: Optional[TaskPlan]) -> bool:
        if not plan or not plan.phases:
            return False

        changed = False
        phases = plan.phases
        running_idx = next(
            (idx for idx, phase in enumerate(phases) if phase.status == PlanPhaseStatus.RUNNING),
            None,
        )

        if running_idx is None:
            next_idx = next(
                (idx for idx, phase in enumerate(phases) if phase.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}),
                None,
            )
            if next_idx is None:
                return False
            phases[next_idx].status = PlanPhaseStatus.RUNNING
            if plan.current_phase_id != phases[next_idx].id:
                plan.current_phase_id = phases[next_idx].id
            return True

        # 仅在第一阶段时推进到下一阶段；避免每轮工具调用都跳阶段。
        if running_idx != 0:
            if plan.current_phase_id != phases[running_idx].id:
                plan.current_phase_id = phases[running_idx].id
                changed = True
            return changed

        next_idx = next(
            (idx for idx, phase in enumerate(phases[1:], start=1) if phase.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}),
            None,
        )
        if next_idx is None:
            if plan.current_phase_id != phases[running_idx].id:
                plan.current_phase_id = phases[running_idx].id
                changed = True
            return changed

        if phases[running_idx].status != PlanPhaseStatus.COMPLETED:
            phases[running_idx].status = PlanPhaseStatus.COMPLETED
            changed = True
        if phases[next_idx].status != PlanPhaseStatus.RUNNING:
            phases[next_idx].status = PlanPhaseStatus.RUNNING
            changed = True
        if plan.current_phase_id != phases[next_idx].id:
            plan.current_phase_id = phases[next_idx].id
            changed = True
        return changed

    @staticmethod
    def _mark_plan_completed(plan: Optional[TaskPlan]) -> bool:
        if not plan:
            return False
        changed = False
        for phase in plan.phases:
            if phase.status != PlanPhaseStatus.COMPLETED:
                phase.status = PlanPhaseStatus.COMPLETED
                changed = True
        if plan.phases:
            last_phase_id = plan.phases[-1].id
            if plan.current_phase_id != last_phase_id:
                plan.current_phase_id = last_phase_id
                changed = True
        return changed

    @staticmethod
    def _transition_plan_to_finalizing(plan: Optional[TaskPlan]) -> bool:
        if not plan or not plan.phases:
            return False

        changed = False
        final_phase = plan.phases[-1]

        for phase in plan.phases[:-1]:
            if phase.status != PlanPhaseStatus.COMPLETED:
                phase.status = PlanPhaseStatus.COMPLETED
                changed = True

        if final_phase.status != PlanPhaseStatus.RUNNING:
            final_phase.status = PlanPhaseStatus.RUNNING
            changed = True

        if plan.current_phase_id != final_phase.id:
            plan.current_phase_id = final_phase.id
            changed = True

        return changed

    @staticmethod
    def _mark_plan_failed(plan: Optional[TaskPlan]) -> bool:
        if not plan:
            return False
        running = next((p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING), None)
        if running:
            running.status = PlanPhaseStatus.FAILED
            plan.current_phase_id = running.id
            return True
        if plan.phases:
            for phase in plan.phases:
                if phase.status == PlanPhaseStatus.PENDING:
                    phase.status = PlanPhaseStatus.FAILED
                    plan.current_phase_id = phase.id
                    return True
        return False

    def _build_messages(self, conversation: Conversation) -> List[Dict[str, Any]]:
        """构建发送给 LLM 的消息列表（带可恢复压缩）"""
        plan_markdown = self._plan_to_markdown(conversation.plan) if conversation.plan else None
        return self._context_manager.build_messages(
            conversation=conversation,
            plan_markdown=plan_markdown,
        )

    @staticmethod
    def _is_tool_blocked_by_takeover(tool_name: str, target: str) -> bool:
        normalized = (target or "all").strip().lower()
        if normalized == "all":
            return True
        if normalized == "terminal":
            return tool_name in {"shell_exec", "execute_code"}
        if normalized == "browser":
            return tool_name.startswith("browser_")
        return True

    def _get_allowed_tools(self, conversation: Conversation) -> List[str]:
        allowed = list(DEFAULT_TOOL_NAMES)
        if conversation.manual_takeover_enabled:
            target = (conversation.manual_takeover_target or "all").strip().lower()
            if target == "all":
                return []
            if target == "terminal":
                allowed = [t for t in allowed if t not in {"shell_exec", "execute_code"}]
            elif target == "browser":
                allowed = [t for t in allowed if not t.startswith("browser_")]
            else:
                return []

        return self._tool_state_machine.get_allowed_tools(conversation, allowed)

    @staticmethod
    def _clamp_positive_int(value: Optional[int], default: int, low: int, high: int) -> int:
        base = default
        if isinstance(value, int):
            base = value
        if base < low:
            base = low
        if base > high:
            base = high
        return base

    @staticmethod
    def _build_tool_signature(name: str, arguments: Dict[str, Any]) -> str:
        try:
            args_json = json.dumps(arguments or {}, ensure_ascii=False, sort_keys=True)
        except Exception:
            args_json = str(arguments)
        return f"{name}:{args_json}"

    @staticmethod
    def _is_repeated_tool_signature(history: List[str], signature: str) -> bool:
        if TOOL_LOOP_REPEAT_THRESHOLD <= 1:
            return True
        if len(history) < TOOL_LOOP_REPEAT_THRESHOLD - 1:
            return False
        tail = history[-(TOOL_LOOP_REPEAT_THRESHOLD - 1):]
        return all(item == signature for item in tail)

    @staticmethod
    def _has_completed_tool_call(conversation: Conversation, tool_name: str) -> bool:
        for msg in reversed(conversation.messages[-60:]):
            if msg.role != MessageRole.TOOL:
                continue
            for tc in msg.tool_calls:
                if tc.name == tool_name and tc.status == ToolCallStatus.COMPLETED:
                    return True
        return False

    @staticmethod
    def _has_completed_reduce_summary_read(conversation: Conversation) -> bool:
        target_suffix = "multi_agent/reduce_summary.md"
        for msg in reversed(conversation.messages[-60:]):
            if msg.role != MessageRole.TOOL:
                continue
            for tc in msg.tool_calls:
                if tc.name != "read_file" or tc.status != ToolCallStatus.COMPLETED:
                    continue
                path_val = tc.arguments.get("path")
                if isinstance(path_val, str) and path_val.strip().endswith(target_suffix):
                    return True
        return False

    def _build_deep_research_instruction(
        self,
        *,
        user_message: str,
        enabled: bool,
        max_concurrency: Optional[int],
        max_items: Optional[int],
        max_iterations: Optional[int],
        spawn_completed: bool,
        reduce_summary_read_completed: bool,
    ) -> Optional[str]:
        if not enabled:
            return None
        if self._is_continue_message(user_message):
            return None

        concurrency = self._clamp_positive_int(max_concurrency, default=3, low=1, high=20)
        items = self._clamp_positive_int(max_items, default=20, low=1, high=100)
        iterations = self._clamp_positive_int(max_iterations, default=4, low=1, high=12)

        if not spawn_completed:
            return (
                "【运行模式】深度研究（子代理并行）已开启。\n"
                "请优先调用 spawn_sub_agents 工具，不要改用 wide_research。\n"
                "你需要从用户请求中提炼 task_template、items、reduce_goal，并立即发起并行。\n"
                f"调用 spawn_sub_agents 时请显式传入: max_concurrency={concurrency}, max_items={items}, max_iterations={iterations}。\n"
                "完成后读取 multi_agent/reduce_summary.md 并给出最终结论。"
            )

        if not reduce_summary_read_completed:
            return (
                "深度研究子代理并行已完成。\n"
                "请读取 `multi_agent/reduce_summary.md` 一次，然后直接输出最终结论。\n"
                "不要再次调用 spawn_sub_agents。"
            )

        return (
            "你已经读取过 `multi_agent/reduce_summary.md`。\n"
            "接下来禁止继续调用工具，请直接输出最终答案。"
        )

    async def run_agent_loop(
        self,
        user_message: str,
        conversation_id: Optional[str] = None,
        record_user_message: bool = True,
        deep_research_enabled: bool = False,
        deep_research_max_concurrency: Optional[int] = None,
        deep_research_max_items: Optional[int] = None,
        deep_research_max_iterations: Optional[int] = None,
    ) -> AsyncGenerator[Dict[str, Any], None]:
        """
        运行 Agent 循环，通过 SSE 事件流返回结果。

        Agent Loop:
        1. 用户输入 -> LLM
        2. LLM 返回文本 -> 输出给用户
        3. LLM 返回工具调用 -> 执行工具 -> 将结果反馈给 LLM -> 回到步骤 2
        4. 直到 LLM 返回纯文本（无工具调用）或达到最大迭代次数
        """
        # 获取或创建对话
        conversation = self.get_or_create_conversation(conversation_id)

        # 添加用户消息（控制指令可选择不写入会话历史）
        if record_user_message:
            user_msg = Message(role=MessageRole.USER, content=user_message)
            conversation.messages.append(user_msg)
        # 进入新一轮执行时，先清除“可继续”标记
        conversation.limit_reached = False
        conversation.continue_message = None

        # 更新计划（初始化或恢复）
        plan_reason = await self._ensure_plan_for_turn(conversation, user_message)

        # 如果是第一条真实用户消息，设置对话标题
        if record_user_message and len(conversation.messages) == 1:
            conversation.title = user_message[:50]
        self._save_conversations()

        # 发送对话 ID
        yield {
            "event": SSEEventType.CONTENT,
            "data": json.dumps({
                "conversation_id": conversation.id,
                "type": "conversation_info"
            }, ensure_ascii=False)
        }

        # 推送计划状态
        if conversation.plan:
            yield {
                "event": SSEEventType.PLAN_UPDATE,
                "data": json.dumps(
                    self._build_plan_update_payload(conversation, reason=plan_reason),
                    ensure_ascii=False,
                ),
            }

        iteration = 0
        completed = False
        limit_notice = ""
        stop_due_invalid_args = False
        stop_due_manual_takeover = False
        invalid_args_fail_count = 0
        last_invalid_tool_name = ""
        last_invalid_reason = ""
        manual_blocked_notice = ""
        stop_due_tool_loop = False
        tool_loop_notice = ""
        recent_tool_signatures: List[str] = []
        while iteration < MAX_ITERATIONS:
            iteration += 1
            stop_due_tool_loop = False

            # 发送思考状态
            yield {
                "event": SSEEventType.THINKING,
                "data": json.dumps({
                    "iteration": iteration,
                    "status": "thinking"
                }, ensure_ascii=False)
            }

            # 调用 LLM（根据当前状态动态约束可用工具）
            messages = self._build_messages(conversation)
            spawn_completed = self._has_completed_tool_call(conversation, "spawn_sub_agents")
            reduce_summary_read_completed = self._has_completed_reduce_summary_read(conversation)
            deep_research_instruction = self._build_deep_research_instruction(
                user_message=user_message,
                enabled=deep_research_enabled,
                max_concurrency=deep_research_max_concurrency,
                max_items=deep_research_max_items,
                max_iterations=deep_research_max_iterations,
                spawn_completed=spawn_completed,
                reduce_summary_read_completed=reduce_summary_read_completed,
            )
            if deep_research_instruction:
                messages = messages + [{
                    "role": "user",
                    "content": deep_research_instruction,
                }]
            allowed_tools = self._get_allowed_tools(conversation)
            # 深度研究分阶段收敛工具，避免在汇总阶段继续发散调用。
            if deep_research_enabled:
                if reduce_summary_read_completed:
                    allowed_tools = []
                elif spawn_completed:
                    allowed_tools = [name for name in allowed_tools if name == "read_file"]
                else:
                    allowed_tools = [name for name in allowed_tools if name == "spawn_sub_agents"]
            content = ""
            tool_calls_data: List[Dict[str, Any]] = []
            content_streamed = False
            stream_error: Optional[str] = None

            stream_queue: asyncio.Queue[Dict[str, Any]] = asyncio.Queue()

            async def _stream_producer() -> None:
                try:
                    async for chunk in chat_completion_stream(
                        messages,
                        use_tools=bool(allowed_tools),
                        allowed_tool_names=allowed_tools if allowed_tools else None,
                    ):
                        if isinstance(chunk, dict):
                            await stream_queue.put(chunk)
                except Exception as exc:
                    await stream_queue.put({"type": "error", "data": str(exc)})
                finally:
                    await stream_queue.put({"type": "__stream_end__"})

            stream_task = asyncio.create_task(_stream_producer())
            try:
                while True:
                    try:
                        chunk = await asyncio.wait_for(
                            stream_queue.get(),
                            timeout=PROGRESS_HEARTBEAT_SECONDS,
                        )
                    except asyncio.TimeoutError:
                        yield {
                            "event": SSEEventType.THINKING,
                            "data": json.dumps({
                                "iteration": iteration,
                                "status": "waiting_llm",
                                "message": "正在等待模型响应…",
                            }, ensure_ascii=False)
                        }
                        continue

                    chunk_type = chunk.get("type", "")
                    if chunk_type == "__stream_end__":
                        break

                    if chunk_type == "content":
                        delta = chunk.get("data", "")
                        if not isinstance(delta, str) or not delta:
                            continue
                        content += delta
                        content_streamed = True
                        yield {
                            "event": SSEEventType.CONTENT,
                            "data": json.dumps({
                                "content": delta,
                                "type": "intermediate",
                            }, ensure_ascii=False)
                        }
                        continue

                    if chunk_type == "tool_call":
                        tc_payload = chunk.get("data")
                        if isinstance(tc_payload, dict):
                            tool_calls_data.append(tc_payload)
                        continue

                    if chunk_type == "done":
                        done_payload = chunk.get("data")
                        if not isinstance(done_payload, dict):
                            continue
                        done_content = done_payload.get("content", "")
                        if isinstance(done_content, str):
                            content = done_content
                        done_tool_calls = done_payload.get("tool_calls", [])
                        if isinstance(done_tool_calls, list):
                            tool_calls_data = done_tool_calls
                        continue

                    if chunk_type == "error":
                        stream_error = str(chunk.get("data", "unknown stream error"))
                        break
            except Exception as exc:
                stream_error = str(exc)
            finally:
                if not stream_task.done():
                    stream_task.cancel()

            # 流式失败时兜底到非流式，避免整轮直接失败。
            if stream_error and not content_streamed and not tool_calls_data:
                logger.warning("LLM stream failed, fallback to non-stream completion: %s", stream_error)
                llm_result = await chat_completion(
                    messages,
                    use_tools=bool(allowed_tools),
                    allowed_tool_names=allowed_tools if allowed_tools else None,
                )
                content = llm_result.get("content", "")
                tool_calls_data = llm_result.get("tool_calls", [])
                content_streamed = False
            elif stream_error and not content and not tool_calls_data:
                content = f"调用 LLM 时出错: {stream_error}"

            if not tool_calls_data:
                # 没有工具调用，直接输出文本内容
                if content:
                    assistant_msg = Message(
                        role=MessageRole.ASSISTANT,
                        content=content
                    )
                    conversation.messages.append(assistant_msg)

                    finalizing_changed = self._transition_plan_to_finalizing(conversation.plan)
                    if finalizing_changed and conversation.plan:
                        self._save_conversations()
                        yield {
                            "event": SSEEventType.PLAN_UPDATE,
                            "data": json.dumps(
                                self._build_plan_update_payload(conversation, reason="finalizing"),
                                ensure_ascii=False,
                            ),
                        }

                    plan_changed = self._mark_plan_completed(conversation.plan)
                    conversation.limit_reached = False
                    conversation.continue_message = None
                    self._save_conversations()

                    if plan_changed and conversation.plan:
                        yield {
                            "event": SSEEventType.PLAN_UPDATE,
                            "data": json.dumps(
                                self._build_plan_update_payload(conversation, reason="completed"),
                                ensure_ascii=False,
                            ),
                        }

                    yield {
                        "event": SSEEventType.CONTENT,
                        "data": json.dumps({
                            "content": content,
                            "type": "final_answer"
                        }, ensure_ascii=False)
                    }

                # Agent 循环结束
                completed = True
                break

            # 有工具调用，推进计划到执行阶段
            if self._transition_plan_to_execution(conversation.plan):
                self._save_conversations()
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._build_plan_update_payload(conversation, reason="executing"),
                            ensure_ascii=False,
                        ),
                    }

            tool_call_objects = []
            tool_call_parse_errors: Dict[str, str] = {}
            tool_call_parse_previews: Dict[str, str] = {}
            for tc_data in tool_calls_data:
                args = tc_data.get("arguments", {})
                if not isinstance(args, dict):
                    args = {}
                    tc_data["parse_error"] = "参数不是 JSON 对象。"

                tc = ToolCall(
                    id=tc_data["id"],
                    name=tc_data["name"],
                    arguments=args,
                    status=ToolCallStatus.RUNNING
                )
                tool_call_objects.append(tc)

                parse_error = tc_data.get("parse_error")
                if parse_error:
                    tool_call_parse_errors[tc.id] = str(parse_error)
                    preview = tc_data.get("raw_arguments_preview")
                    if preview:
                        tool_call_parse_previews[tc.id] = str(preview)

            # 记录 assistant 消息（包含工具调用）
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=content or "",
                tool_calls=tool_call_objects
            )
            conversation.messages.append(assistant_msg)
            self._save_conversations()

            # 如果有文本内容且未在流式阶段输出，则补发一次
            if content and not content_streamed:
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({
                        "content": content,
                        "type": "intermediate"
                    }, ensure_ascii=False)
                }

            # 逐个处理工具调用：每轮只允许执行一个真实工具动作，其他标记为跳过
            for tc_index, tc in enumerate(tool_call_objects):
                # 通知前端工具调用开始
                yield {
                    "event": SSEEventType.TOOL_CALL,
                    "data": json.dumps({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "status": "running"
                    }, ensure_ascii=False)
                }

                if tc_index > 0:
                    result = (
                        "工具调用被跳过：当前执行策略为“每轮仅执行 1 个工具动作”，"
                        "请在下一轮继续。"
                    )
                    tc.result = result
                    tc.status = ToolCallStatus.FAILED
                elif (
                    conversation.manual_takeover_enabled
                    and self._is_tool_blocked_by_takeover(
                        tc.name,
                        conversation.manual_takeover_target,
                    )
                ):
                    reason = (
                        f"当前处于手动接管模式（{conversation.manual_takeover_target}），"
                        "已暂停 Agent 自动工具调用。"
                    )
                    result = f"工具执行已阻断: {reason}"
                    tc.result = result
                    tc.status = ToolCallStatus.FAILED
                    stop_due_manual_takeover = True
                    manual_blocked_notice = (
                        "当前是手动接管模式，我已暂停自动执行。"
                        "\n你可以在右侧直接操作计算机；操作完成后点击“释放接管”再让我继续。"
                    )
                    await event_bus.publish(SandboxEvent(
                        "manual_blocked_tool_call",
                        {
                            "tool_name": tc.name,
                            "reason": reason,
                        },
                        window_id="computer_control",
                        conversation_id=conversation.id,
                    ))
                else:
                    parse_error = tool_call_parse_errors.get(tc.id)
                    if parse_error:
                        preview = tool_call_parse_previews.get(tc.id, "")
                        result = (
                            f"工具执行失败: 模型生成的 `{tc.name}` 参数不是合法 JSON。"
                            f"原因: {parse_error}"
                            "。这通常是因为内容过长导致参数被截断。"
                        )
                        if preview:
                            result += f"\n参数片段: {preview}"
                        tc.result = result
                        tc.status = ToolCallStatus.FAILED
                        invalid_args_fail_count += 1
                        last_invalid_tool_name = tc.name
                        last_invalid_reason = "parse_error"
                    else:
                        # 执行工具（传递 conversation_id 实现会话隔离）
                        try:
                            signature = self._build_tool_signature(tc.name, tc.arguments)
                            if self._is_repeated_tool_signature(recent_tool_signatures, signature):
                                result = (
                                    f"工具执行已阻断: 检测到 `{tc.name}` 连续重复调用，疑似进入死循环。"
                                    "请基于已有信息直接输出最终结论，不要再次调用同一工具。"
                                )
                                tc.result = result
                                tc.status = ToolCallStatus.FAILED
                                stop_due_tool_loop = True
                                tool_loop_notice = (
                                    "我检测到工具调用进入重复循环，已自动停止重复动作并准备直接给出结论。"
                                )
                            else:
                                recent_tool_signatures.append(signature)
                                if len(recent_tool_signatures) > TOOL_LOOP_WINDOW:
                                    recent_tool_signatures = recent_tool_signatures[-TOOL_LOOP_WINDOW:]

                                tool_task = asyncio.create_task(
                                    execute_tool(tc.name, tc.arguments, conversation_id=conversation.id)
                                )
                                while True:
                                    try:
                                        result = await asyncio.wait_for(
                                            asyncio.shield(tool_task),
                                            timeout=PROGRESS_HEARTBEAT_SECONDS,
                                        )
                                        break
                                    except asyncio.TimeoutError:
                                        yield {
                                            "event": SSEEventType.THINKING,
                                            "data": json.dumps({
                                                "iteration": iteration,
                                                "status": "waiting_tool",
                                                "tool_name": tc.name,
                                                "message": f"工具 `{tc.name}` 执行中…",
                                            }, ensure_ascii=False)
                                        }
                                tc.result = result
                                tc.status = ToolCallStatus.COMPLETED
                        except Exception as e:
                            err_text = str(e)
                            result = f"工具执行失败: {err_text}"
                            tc.result = result
                            tc.status = ToolCallStatus.FAILED

                            if (
                                "缺少必填参数" in err_text
                                or "参数不能为空" in err_text
                                or "参数类型错误" in err_text
                                or "参数格式错误" in err_text
                            ):
                                invalid_args_fail_count += 1
                                last_invalid_tool_name = tc.name
                                last_invalid_reason = "invalid_args"

                # 通知前端工具调用结果
                yield {
                    "event": SSEEventType.TOOL_RESULT,
                    "data": json.dumps({
                        "id": tc.id,
                        "name": tc.name,
                        "result": result[:2000],  # 限制发送长度
                        "status": tc.status.value
                    }, ensure_ascii=False)
                }

                # 将工具结果添加到对话历史
                tool_msg = Message(
                    role=MessageRole.TOOL,
                    content=result,
                    tool_calls=[ToolCall(
                        id=tc.id,
                        name=tc.name,
                        arguments=tc.arguments,
                        result=result,
                        status=tc.status
                    )]
                )
                conversation.messages.append(tool_msg)
                self._save_conversations()

                if stop_due_manual_takeover:
                    break

                if stop_due_tool_loop:
                    break

                # 连续参数错误时停止自动重试，避免空参数死循环
                if invalid_args_fail_count >= 2:
                    stop_due_invalid_args = True
                    break

            if stop_due_manual_takeover:
                conversation.messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=manual_blocked_notice
                    )
                )
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({
                        "content": manual_blocked_notice,
                        "type": "final_answer"
                    }, ensure_ascii=False)
                }
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._build_plan_update_payload(conversation, reason="paused_manual_takeover"),
                            ensure_ascii=False,
                        ),
                    }
                completed = True
                break

            if stop_due_invalid_args:
                if last_invalid_reason == "parse_error":
                    invalid_notice = (
                        f"工具 `{last_invalid_tool_name}` 连续多次生成了无效参数（疑似输出过长被截断），"
                        "我先停止自动重试，避免死循环。"
                        "\n你可以点击“继续”，我会改为更短、更分步地写入文件。"
                    )
                else:
                    invalid_notice = (
                        f"工具 `{last_invalid_tool_name}` 连续多次缺少必要参数，"
                        "我无法继续自动执行。请明确告诉我参数后我再继续。"
                        "\n例如：`将 XXX 写入 plane_game/game.js`。"
                    )
                conversation.messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=invalid_notice
                    )
                )
                plan_failed = self._mark_plan_failed(conversation.plan)
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()
                if plan_failed and conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._build_plan_update_payload(conversation, reason="failed_invalid_args"),
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({
                        "content": invalid_notice,
                        "type": "final_answer"
                    }, ensure_ascii=False)
                }
                completed = True
                break

            if stop_due_tool_loop:
                loop_messages = self._build_messages(conversation) + [{
                    "role": "user",
                    "content": (
                        "检测到你刚才重复调用了同一个工具，已被系统阻断。"
                        "请不要再调用任何工具，直接基于现有结果给出最终结论。"
                    ),
                }]
                loop_final_resp = await chat_completion(loop_messages, use_tools=False)
                loop_final = (loop_final_resp.get("content", "") or "").strip()
                if not loop_final:
                    loop_final = "检测到工具调用重复循环，已停止自动工具调用。请查看现有结果并继续。"
                if tool_loop_notice and tool_loop_notice not in loop_final:
                    loop_final = f"{tool_loop_notice}\n\n{loop_final}"

                conversation.messages.append(
                    Message(
                        role=MessageRole.ASSISTANT,
                        content=loop_final
                    )
                )

                finalizing_changed = self._transition_plan_to_finalizing(conversation.plan)
                plan_changed = self._mark_plan_completed(conversation.plan)
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()

                if finalizing_changed and conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._build_plan_update_payload(conversation, reason="finalizing_tool_loop"),
                            ensure_ascii=False,
                        ),
                    }
                if plan_changed and conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._build_plan_update_payload(conversation, reason="completed_tool_loop"),
                            ensure_ascii=False,
                        ),
                    }

                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({
                        "content": loop_final,
                        "type": "final_answer"
                    }, ensure_ascii=False)
                }
                completed = True
                break

        if not completed and iteration >= MAX_ITERATIONS:
            limit_notice = (
                f"已达到单次最大调用轮数（{MAX_ITERATIONS} 轮）。"
                "你可以点击“继续”让 Agent 在当前上下文中接着执行。"
            )
            conversation.messages.append(
                Message(
                    role=MessageRole.ASSISTANT,
                    content=limit_notice
                )
            )
            conversation.limit_reached = True
            conversation.continue_message = limit_notice
            self._save_conversations()
            if conversation.plan:
                yield {
                    "event": SSEEventType.PLAN_UPDATE,
                    "data": json.dumps(
                        self._build_plan_update_payload(conversation, reason="limit_reached"),
                        ensure_ascii=False,
                    ),
                }
            yield {
                "event": SSEEventType.CONTENT,
                "data": json.dumps({
                    "content": limit_notice,
                    "type": "final_answer"
                }, ensure_ascii=False)
            }

        # 发送完成事件
        yield {
            "event": SSEEventType.DONE,
            "data": json.dumps({
                "conversation_id": conversation.id,
                "iterations": iteration,
                "limit_reached": (not completed and iteration >= MAX_ITERATIONS),
                "max_iterations": MAX_ITERATIONS,
                "continue_message": limit_notice
            }, ensure_ascii=False)
        }


# 全局 Agent 引擎实例
agent_engine = AgentEngine()
