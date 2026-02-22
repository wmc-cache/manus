"""Agent 核心引擎 - 实现 Agent Loop"""
import asyncio
import json
import os
from pathlib import Path
from typing import AsyncGenerator, Dict, Any, List, Optional

from agent.tools import execute_tool
from llm.deepseek import chat_completion
from models.schemas import (
    SSEEventType,
    Conversation,
    Message,
    MessageRole,
    ToolCall,
    ToolCallStatus,
    TaskPlan,
    PlanPhase,
    PlanPhaseStatus,
)
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_workspace_root


MAX_ITERATIONS = 10  # 最大迭代次数，防止无限循环
DEFAULT_CONVERSATION_STORE = "/tmp/manus_workspace/conversations.json"
MAX_CONTEXT_MESSAGES = int(os.environ.get("MANUS_MAX_CONTEXT_MESSAGES", "40"))
MAX_RECENT_MESSAGE_CHARS = int(os.environ.get("MANUS_MAX_RECENT_MESSAGE_CHARS", "4000"))
MAX_OLD_MESSAGE_CHARS = int(os.environ.get("MANUS_MAX_OLD_MESSAGE_CHARS", "1200"))
TODO_FILENAME = "todo.md"
CONTINUE_MESSAGES = {"继续", "继续。", "continue", "continue.", "go on"}
DEFAULT_TOOL_NAMES = [
    "web_search",
    "wide_research",
    "shell_exec",
    "execute_code",
    "browser_navigate",
    "browser_screenshot",
    "browser_get_content",
    "read_file",
    "write_file",
]


class AgentEngine:
    """Agent 核心引擎"""

    def __init__(self):
        self.conversations: Dict[str, Conversation] = {}
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self._store_path = Path(os.environ.get("MANUS_CONVERSATIONS_FILE", DEFAULT_CONVERSATION_STORE))
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
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
        except Exception:
            # 不因持久化异常中断主流程
            pass

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

    def _create_default_plan(self, user_message: str) -> TaskPlan:
        goal = user_message.strip() or "完成用户请求"
        if len(goal) > 180:
            goal = goal[:180] + "..."
        phases = [
            PlanPhase(id=1, title="理解需求并确认执行路径", status=PlanPhaseStatus.RUNNING),
            PlanPhase(id=2, title="调用工具逐步完成任务", status=PlanPhaseStatus.PENDING),
            PlanPhase(id=3, title="整理结果并给出最终回复", status=PlanPhaseStatus.PENDING),
        ]
        return TaskPlan(goal=goal, phases=phases, current_phase_id=1)

    @staticmethod
    def _is_continue_message(user_message: str) -> bool:
        return user_message.strip().lower() in CONTINUE_MESSAGES

    def _ensure_plan_for_turn(self, conversation: Conversation, user_message: str) -> str:
        if conversation.plan is None or not self._is_continue_message(user_message):
            conversation.plan = self._create_default_plan(user_message)
            return "initialized"

        plan = conversation.plan
        if not plan.phases:
            conversation.plan = self._create_default_plan(user_message)
            return "initialized"

        # 若无运行阶段，则继续当前未完成阶段
        running_phase = next((p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING), None)
        if running_phase is None:
            pending = next((p for p in plan.phases if p.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}), None)
            if pending:
                pending.status = PlanPhaseStatus.RUNNING
                plan.current_phase_id = pending.id
            else:
                # 已全部完成，继续时重开执行阶段
                exec_phase = next((p for p in plan.phases if p.id == 2), None)
                if exec_phase:
                    exec_phase.status = PlanPhaseStatus.RUNNING
                    plan.current_phase_id = exec_phase.id
        return "resumed"

    @staticmethod
    def _transition_plan_to_execution(plan: Optional[TaskPlan]) -> bool:
        if not plan:
            return False
        changed = False
        phase1 = next((p for p in plan.phases if p.id == 1), None)
        phase2 = next((p for p in plan.phases if p.id == 2), None)
        if phase1 and phase1.status != PlanPhaseStatus.COMPLETED:
            phase1.status = PlanPhaseStatus.COMPLETED
            changed = True
        if phase2 and phase2.status != PlanPhaseStatus.RUNNING:
            phase2.status = PlanPhaseStatus.RUNNING
            changed = True
        if phase2 and plan.current_phase_id != phase2.id:
            plan.current_phase_id = phase2.id
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
        """构建发送给 LLM 的消息列表（带基础上下文压缩）"""
        messages: List[Dict[str, Any]] = []
        all_msgs = conversation.messages
        sliced = all_msgs[-MAX_CONTEXT_MESSAGES:] if len(all_msgs) > MAX_CONTEXT_MESSAGES else all_msgs

        omitted = len(all_msgs) - len(sliced)
        if omitted > 0:
            messages.append({
                "role": "system",
                "content": f"上下文已压缩：省略了更早的 {omitted} 条消息，请聚焦当前任务。",
            })

        recent_start = max(0, len(sliced) - 12)
        for idx, msg in enumerate(sliced):
            limit = MAX_RECENT_MESSAGE_CHARS if idx >= recent_start else MAX_OLD_MESSAGE_CHARS

            if msg.role == MessageRole.USER:
                messages.append({"role": "user", "content": self._clip_text(msg.content, limit)})

            elif msg.role == MessageRole.ASSISTANT:
                entry: Dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    entry["content"] = self._clip_text(msg.content, limit)
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                    if not entry.get("content"):
                        entry["content"] = ""
                messages.append(entry)

            elif msg.role == MessageRole.TOOL:
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_calls[0].id if msg.tool_calls else "",
                    "content": self._clip_text(msg.content, limit),
                })

        if conversation.plan:
            plan_markdown = self._clip_text(self._plan_to_markdown(conversation.plan), 3000)
            messages.append({
                "role": "system",
                "content": (
                    "你必须严格对齐当前任务计划（todo.md）执行，优先推进当前阶段：\n"
                    f"{plan_markdown}"
                ),
            })

        return messages

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
        if not conversation.manual_takeover_enabled:
            return allowed

        target = (conversation.manual_takeover_target or "all").strip().lower()
        if target == "all":
            return []
        if target == "terminal":
            return [t for t in allowed if t not in {"shell_exec", "execute_code"}]
        if target == "browser":
            return [t for t in allowed if not t.startswith("browser_")]
        return []

    async def run_agent_loop(
        self,
        user_message: str,
        conversation_id: Optional[str] = None,
        record_user_message: bool = True,
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
        plan_reason = self._ensure_plan_for_turn(conversation, user_message)

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
        while iteration < MAX_ITERATIONS:
            iteration += 1

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
            allowed_tools = self._get_allowed_tools(conversation)
            llm_result = await chat_completion(
                messages,
                use_tools=bool(allowed_tools),
                allowed_tool_names=allowed_tools if allowed_tools else None,
            )

            content = llm_result.get("content", "")
            tool_calls_data = llm_result.get("tool_calls", [])

            if not tool_calls_data:
                # 没有工具调用，直接输出文本内容
                if content:
                    assistant_msg = Message(
                        role=MessageRole.ASSISTANT,
                        content=content
                    )
                    conversation.messages.append(assistant_msg)

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

            # 如果有文本内容，先输出
            if content:
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
                            result = await execute_tool(tc.name, tc.arguments, conversation_id=conversation.id)
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
