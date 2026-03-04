"""
Agent 核心引擎（重构后）。

重构说明：
- 会话持久化 → agent/conversation_store.py
- 计划状态机 → agent/plan_manager.py
- 工具执行编排 → agent/tool_executor.py
- 消息构建 → agent/message_builder.py
- 依赖注入 → agent/dependencies.py
- 本文件仅保留 AgentEngine 类和 run_agent_loop 核心循环
"""

import asyncio
import json
import logging
from typing import AsyncGenerator, Dict, Any, List, Optional

from config.settings import settings
from agent.context_manager import build_default_context_manager, ToolStateMachine
from agent.conversation_store import ConversationStore
from agent.plan_manager import PlanManager
from agent.tool_executor import ToolExecutor
from agent.message_builder import MessageBuilder
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
    PlanPhaseStatus,
)
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_workspace_root


logger = logging.getLogger(__name__)


class AgentEngine:
    """
    Agent 核心引擎。

    重构后通过依赖注入接收子系统，也支持无参构造（向后兼容）。
    """

    def __init__(
        self,
        store: Optional[ConversationStore] = None,
        plan_mgr: Optional[PlanManager] = None,
        tool_exec: Optional[ToolExecutor] = None,
        msg_builder: Optional[MessageBuilder] = None,
    ):
        # 会话存储
        self._store = store or ConversationStore()
        # 计划管理
        if plan_mgr is None:
            planner = Planner(llm_func=chat_completion)
            plan_mgr = PlanManager(planner=planner, use_llm=settings.agent.plan_use_llm)
        self._plan_mgr = plan_mgr
        # 工具执行
        self._tool_exec = tool_exec or ToolExecutor(execute_tool_fn=execute_tool)
        # 消息构建
        self._msg_builder = msg_builder or MessageBuilder()
        # 上下文管理器（保留原有实现）
        self._context_manager = build_default_context_manager(
            max_context_messages=settings.agent.max_context_messages,
            max_recent_message_chars=settings.agent.max_recent_message_chars,
            max_old_message_chars=settings.agent.max_old_message_chars,
        )
        self._tool_state_machine = ToolStateMachine()

    # ---- 代理属性到 store 的转发 ----

    @property
    def conversations(self) -> Dict[str, Conversation]:
        return self._store.conversations

    def get_conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        return self._store.get_conversation_lock(conversation_id)

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Conversation:
        return self._store.get_or_create_conversation(conversation_id)

    async def delete_conversation(self, conversation_id: str):
        return await self._store.delete_conversation(conversation_id)

    def repair_conversation_titles(self):
        return self._store.repair_conversation_titles()

    def _save_conversations(self):
        self._store.save_conversations()

    # ---- 内部辅助方法 ----

    def _build_messages(self, conversation: Conversation) -> List[Dict[str, Any]]:
        """构建发送给 LLM 的消息列表（使用 context_manager）"""
        plan_markdown = self._plan_mgr.to_markdown(conversation.plan) if conversation.plan else None
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

    def _get_allowed_tools(self, conversation: Conversation, *, deep_research_enabled: bool = False) -> List[str]:
        allowed = list(settings.agent.default_tool_names)
        if not deep_research_enabled:
            allowed = [t for t in allowed if t != "spawn_sub_agents"]
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
        base = value if isinstance(value, int) else default
        return max(low, min(high, base))

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

    @staticmethod
    def _is_continue_message(user_message: str) -> bool:
        return user_message.strip().lower() in settings.agent.continue_messages

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
                "【重要：深度研究模式已开启】\n\n"
                "你现在处于深度研究模式。你必须使用 spawn_sub_agents 工具来并行研究。\n\n"
                "**你必须立即调用 spawn_sub_agents 工具**，不要使用其他工具。\n"
                "不要使用 web_search、shell_exec、write_file 等工具来手动研究。\n"
                "不要自己搜索信息，让子代理去做。\n\n"
                "spawn_sub_agents 的参数说明：\n"
                "- task_template: 子代理的任务模板，用 {item} 作为占位符\n"
                "- items: 要并行研究的子主题列表（字符串数组）\n"
                "- reduce_goal: 汇总目标，描述如何将所有子代理结果合并\n"
                f"- max_concurrency: {concurrency}\n"
                f"- max_items: {items}\n"
                f"- max_iterations: {iterations}\n\n"
                "示例调用：\n"
                "spawn_sub_agents(\n"
                '  task_template="请深入研究 {item} 的最新进展、关键技术和应用场景",\n'
                '  items=["主题1", "主题2", "主题3"],\n'
                '  reduce_goal="综合所有研究结果，撰写一份全面的分析报告"\n'
                ")\n\n"
                "请根据用户的请求，拆分出合理的子主题列表，然后立即调用 spawn_sub_agents。"
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

    # ---- 核心 Agent 循环 ----

    async def run_agent_loop(
        self,
        user_message: str,
        conversation_id: Optional[str] = None,
        record_user_message: bool = True,
        uploaded_images: Optional[List[Dict[str, Any]]] = None,
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
        MAX_ITERATIONS = settings.agent.max_iterations
        PROGRESS_HEARTBEAT_SECONDS = settings.agent.progress_heartbeat_seconds
        TOOL_LOOP_WINDOW = settings.agent.tool_loop_window

        # 规范化上传图片
        normalized_images: List[Dict[str, Any]] = []
        if uploaded_images:
            for image in uploaded_images:
                if not isinstance(image, dict):
                    continue
                path = str(image.get("path", "")).strip()
                name = str(image.get("name", "")).strip()
                mime_type = str(image.get("mime_type", "")).strip() or "application/octet-stream"
                size_bytes = image.get("size_bytes")
                normalized_images.append({
                    "path": path,
                    "name": name,
                    "mime_type": mime_type,
                    "size_bytes": int(size_bytes) if isinstance(size_bytes, int) else None,
                })

        effective_user_message = (user_message or "").strip()
        if not effective_user_message and normalized_images:
            effective_user_message = "请分析我上传的图片。"

        logger.info(
            "[AgentLoop] Starting: deep_research_enabled=%s, message=%s",
            deep_research_enabled, effective_user_message[:80]
        )
        conversation = self.get_or_create_conversation(conversation_id)

        # 添加用户消息
        if record_user_message:
            user_msg = Message(
                role=MessageRole.USER,
                content=effective_user_message,
                images=normalized_images,
            )
            conversation.messages.append(user_msg)

        conversation.limit_reached = False
        conversation.continue_message = None

        # 更新计划
        plan_reason = await self._plan_mgr.ensure_plan_for_turn(conversation, effective_user_message)

        if record_user_message:
            self._store._maybe_refresh_conversation_title(conversation, preferred_source=effective_user_message)
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
                    self._plan_mgr.build_update_payload(conversation, reason=plan_reason),
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
            completed_tools_this_iteration = 0

            yield {
                "event": SSEEventType.THINKING,
                "data": json.dumps({
                    "iteration": iteration,
                    "status": "thinking"
                }, ensure_ascii=False)
            }

            # 调用 LLM
            messages = self._build_messages(conversation)
            spawn_completed = self._has_completed_tool_call(conversation, "spawn_sub_agents")
            reduce_summary_read_completed = self._has_completed_reduce_summary_read(conversation)
            deep_research_instruction = self._build_deep_research_instruction(
                user_message=effective_user_message,
                enabled=deep_research_enabled,
                max_concurrency=deep_research_max_concurrency,
                max_items=deep_research_max_items,
                max_iterations=deep_research_max_iterations,
                spawn_completed=spawn_completed,
                reduce_summary_read_completed=reduce_summary_read_completed,
            )
            if deep_research_instruction:
                messages = messages + [{"role": "user", "content": deep_research_instruction}]

            allowed_tools = self._get_allowed_tools(
                conversation, deep_research_enabled=deep_research_enabled,
            )
            if deep_research_enabled:
                if reduce_summary_read_completed:
                    allowed_tools = []
                elif spawn_completed:
                    allowed_tools = [name for name in allowed_tools if name == "read_file"]
                else:
                    allowed_tools = ["spawn_sub_agents"]

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

            # 流式失败时兜底到非流式
            if stream_error and not content_streamed and not tool_calls_data:
                logger.warning("LLM stream failed, fallback: %s", stream_error)
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
                # 没有工具调用 → 输出最终文本
                if content:
                    assistant_msg = Message(role=MessageRole.ASSISTANT, content=content)
                    conversation.messages.append(assistant_msg)

                    finalizing_changed = self._plan_mgr.transition_to_finalizing(conversation.plan)
                    if finalizing_changed and conversation.plan:
                        self._save_conversations()
                        yield {
                            "event": SSEEventType.PLAN_UPDATE,
                            "data": json.dumps(
                                self._plan_mgr.build_update_payload(conversation, reason="finalizing"),
                                ensure_ascii=False,
                            ),
                        }

                    plan_changed = self._plan_mgr.mark_completed(conversation.plan)
                    conversation.limit_reached = False
                    conversation.continue_message = None
                    self._save_conversations()

                    if plan_changed and conversation.plan:
                        yield {
                            "event": SSEEventType.PLAN_UPDATE,
                            "data": json.dumps(
                                self._plan_mgr.build_update_payload(conversation, reason="completed"),
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

                completed = True
                break

            # 有工具调用 → 推进计划到执行阶段
            if self._plan_mgr.transition_to_execution(conversation.plan):
                self._save_conversations()
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._plan_mgr.build_update_payload(conversation, reason="executing"),
                            ensure_ascii=False,
                        ),
                    }

            # 解析工具调用
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
                    status=ToolCallStatus.RUNNING,
                )
                tool_call_objects.append(tc)

                parse_error = tc_data.get("parse_error")
                if parse_error:
                    tool_call_parse_errors[tc.id] = str(parse_error)
                    preview = tc_data.get("raw_arguments_preview")
                    if preview:
                        tool_call_parse_previews[tc.id] = str(preview)

            # 记录 assistant 消息
            assistant_msg = Message(
                role=MessageRole.ASSISTANT,
                content=content or "",
                tool_calls=tool_call_objects,
            )
            conversation.messages.append(assistant_msg)
            self._save_conversations()

            if content and not content_streamed:
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({
                        "content": content,
                        "type": "intermediate"
                    }, ensure_ascii=False)
                }

            # 使用 ToolExecutor 分类工具调用
            parallel_batch, serial_queue = self._tool_exec.classify_tool_calls(tool_call_objects)

            # 并行执行安全工具
            if parallel_batch:
                for tc in parallel_batch:
                    yield {
                        "event": SSEEventType.TOOL_CALL,
                        "data": json.dumps({
                            "id": tc.id,
                            "name": tc.name,
                            "arguments": tc.arguments,
                            "status": "running"
                        }, ensure_ascii=False)
                    }

                parallel_results = await self._tool_exec.execute_parallel(
                    parallel_batch, conversation.id,
                    tool_call_parse_errors, tool_call_parse_previews,
                )

                for tc, result, status in parallel_results:
                    tc.result = result
                    tc.status = status

                    if status == ToolCallStatus.COMPLETED:
                        completed_tools_this_iteration += 1
                        signature = ToolExecutor.build_tool_signature(tc.name, tc.arguments)
                        recent_tool_signatures.append(signature)
                        if len(recent_tool_signatures) > TOOL_LOOP_WINDOW:
                            recent_tool_signatures = recent_tool_signatures[-TOOL_LOOP_WINDOW:]

                    yield {
                        "event": SSEEventType.TOOL_RESULT,
                        "data": json.dumps({
                            "id": tc.id,
                            "name": tc.name,
                            "result": result[:2000],
                            "status": tc.status.value
                        }, ensure_ascii=False)
                    }

                    tool_msg = Message(
                        role=MessageRole.TOOL,
                        content=result,
                        tool_calls=[ToolCall(
                            id=tc.id, name=tc.name,
                            arguments=tc.arguments, result=result, status=tc.status,
                        )],
                    )
                    conversation.messages.append(tool_msg)

                self._save_conversations()

            # 串行执行有副作用的工具
            serial_executed = False
            for tc in serial_queue:
                yield {
                    "event": SSEEventType.TOOL_CALL,
                    "data": json.dumps({
                        "id": tc.id,
                        "name": tc.name,
                        "arguments": tc.arguments,
                        "status": "running"
                    }, ensure_ascii=False)
                }

                if serial_executed:
                    result = "工具调用被跳过：当前轮次已执行了一个有副作用的工具，请在下一轮继续。"
                    tc.result = result
                    tc.status = ToolCallStatus.FAILED
                elif (
                    conversation.manual_takeover_enabled
                    and self._is_tool_blocked_by_takeover(tc.name, conversation.manual_takeover_target)
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
                        "\n你可以在右侧直接操作计算机；操作完成后点击'释放接管'再让我继续。"
                    )
                    await event_bus.publish(SandboxEvent(
                        "manual_blocked_tool_call",
                        {"tool_name": tc.name, "reason": reason},
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
                        try:
                            signature = ToolExecutor.build_tool_signature(tc.name, tc.arguments)
                            if ToolExecutor.is_repeated_signature(recent_tool_signatures, signature):
                                result = (
                                    f"工具执行已阻断: 检测到 `{tc.name}` 连续重复调用，疑似进入死循环。"
                                    "请基于已有信息直接输出最终结论，不要再次调用同一工具。"
                                )
                                tc.result = result
                                tc.status = ToolCallStatus.FAILED
                                stop_due_tool_loop = True
                                tool_loop_notice = "我检测到工具调用进入重复循环，已自动停止重复动作并准备直接给出结论。"
                            else:
                                recent_tool_signatures.append(signature)
                                if len(recent_tool_signatures) > TOOL_LOOP_WINDOW:
                                    recent_tool_signatures = recent_tool_signatures[-TOOL_LOOP_WINDOW:]

                                result, status, heartbeats = await self._tool_exec.execute_serial_one(
                                    tc, conversation.id,
                                    tool_call_parse_errors, tool_call_parse_previews,
                                    heartbeat_seconds=PROGRESS_HEARTBEAT_SECONDS,
                                )
                                for hb in heartbeats:
                                    yield hb
                                tc.result = result
                                tc.status = status
                                if status == ToolCallStatus.COMPLETED:
                                    completed_tools_this_iteration += 1
                        except Exception as e:
                            err_text = str(e)
                            result = f"工具执行失败: {err_text}"
                            tc.result = result
                            tc.status = ToolCallStatus.FAILED

                            if any(kw in err_text for kw in ["缺少必填参数", "参数不能为空", "参数类型错误", "参数格式错误"]):
                                invalid_args_fail_count += 1
                                last_invalid_tool_name = tc.name
                                last_invalid_reason = "invalid_args"

                    serial_executed = True

                yield {
                    "event": SSEEventType.TOOL_RESULT,
                    "data": json.dumps({
                        "id": tc.id,
                        "name": tc.name,
                        "result": result[:2000],
                        "status": tc.status.value
                    }, ensure_ascii=False)
                }

                tool_msg = Message(
                    role=MessageRole.TOOL,
                    content=result,
                    tool_calls=[ToolCall(
                        id=tc.id, name=tc.name,
                        arguments=tc.arguments, result=result, status=tc.status,
                    )],
                )
                conversation.messages.append(tool_msg)
                self._save_conversations()

                if stop_due_manual_takeover:
                    break
                if stop_due_tool_loop:
                    break
                if invalid_args_fail_count >= 2:
                    stop_due_invalid_args = True
                    break

            # 处理各种停止条件
            if stop_due_manual_takeover:
                conversation.messages.append(Message(role=MessageRole.ASSISTANT, content=manual_blocked_notice))
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({"content": manual_blocked_notice, "type": "final_answer"}, ensure_ascii=False)
                }
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._plan_mgr.build_update_payload(conversation, reason="paused_manual_takeover"),
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
                        "\n你可以点击'继续'，我会改为更短、更分步地写入文件。"                   )
                else:
                    invalid_notice = (
                        f"工具 `{last_invalid_tool_name}` 连续多次缺少必要参数，"
                        "我无法继续自动执行。请明确告诉我参数后我再继续。"
                        "\n例如：`将 XXX 写入 plane_game/game.js`。"
                    )
                conversation.messages.append(Message(role=MessageRole.ASSISTANT, content=invalid_notice))
                self._plan_mgr.mark_failed(conversation.plan)
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._plan_mgr.build_update_payload(conversation, reason="failed_invalid_args"),
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({"content": invalid_notice, "type": "final_answer"}, ensure_ascii=False)
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

                conversation.messages.append(Message(role=MessageRole.ASSISTANT, content=loop_final))
                self._plan_mgr.transition_to_finalizing(conversation.plan)
                self._plan_mgr.mark_completed(conversation.plan)
                conversation.limit_reached = False
                conversation.continue_message = None
                self._save_conversations()

                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._plan_mgr.build_update_payload(conversation, reason="completed_tool_loop"),
                            ensure_ascii=False,
                        ),
                    }
                yield {
                    "event": SSEEventType.CONTENT,
                    "data": json.dumps({"content": loop_final, "type": "final_answer"}, ensure_ascii=False)
                }
                completed = True
                break

            # 自动推进计划阶段
            has_pending_phase = bool(
                conversation.plan
                and any(phase.status == PlanPhaseStatus.PENDING for phase in conversation.plan.phases)
            )
            if completed_tools_this_iteration > 0 and has_pending_phase and self._plan_mgr.advance_phase(conversation.plan):
                self._save_conversations()
                if conversation.plan:
                    yield {
                        "event": SSEEventType.PLAN_UPDATE,
                        "data": json.dumps(
                            self._plan_mgr.build_update_payload(conversation, reason="phase_advanced"),
                            ensure_ascii=False,
                        ),
                    }

        # 达到最大迭代次数
        if not completed and iteration >= MAX_ITERATIONS:
            limit_notice = (
                f"已达到单次最大调用轮数（{MAX_ITERATIONS} 轮）。"
                "你可以点击'继续'让 Agent 在当前上下文中接着执行。"
            )
            conversation.messages.append(Message(role=MessageRole.ASSISTANT, content=limit_notice))
            conversation.limit_reached = True
            conversation.continue_message = limit_notice
            self._save_conversations()
            if conversation.plan:
                yield {
                    "event": SSEEventType.PLAN_UPDATE,
                    "data": json.dumps(
                        self._plan_mgr.build_update_payload(conversation, reason="limit_reached"),
                        ensure_ascii=False,
                    ),
                }
            yield {
                "event": SSEEventType.CONTENT,
                "data": json.dumps({"content": limit_notice, "type": "final_answer"}, ensure_ascii=False)
            }

        # 发送完成事件
        yield {
            "event": SSEEventType.DONE,
            "data": json.dumps({
                "conversation_id": conversation.id,
                "iterations": iteration,
                "limit_reached": (not completed and iteration >= MAX_ITERATIONS),
                "max_iterations": MAX_ITERATIONS,
                "continue_message": limit_notice,
            }, ensure_ascii=False)
        }


# ---------------------------------------------------------------------------
# 全局 Agent 引擎实例（向后兼容）
# ---------------------------------------------------------------------------
agent_engine = AgentEngine()
