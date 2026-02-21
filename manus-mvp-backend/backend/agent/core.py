"""Agent 核心引擎 - 实现 Agent Loop"""
import json
import asyncio
from typing import AsyncGenerator, Dict, Any, List, Optional
from models.schemas import (
    SSEEventType, Conversation, Message, MessageRole,
    ToolCall, ToolCallStatus, TaskPlan, PlanPhase, PlanPhaseStatus
)
from llm.deepseek import chat_completion, chat_completion_stream
from agent.tools import execute_tool


MAX_ITERATIONS = 10  # 最大迭代次数，防止无限循环


class AgentEngine:
    """Agent 核心引擎"""

    def __init__(self):
        self.conversations: Dict[str, Conversation] = {}

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Conversation:
        """获取或创建对话"""
        if conversation_id and conversation_id in self.conversations:
            return self.conversations[conversation_id]
        conv = Conversation()
        self.conversations[conv.id] = conv
        return conv

    def _build_messages(self, conversation: Conversation) -> List[Dict[str, Any]]:
        """构建发送给 LLM 的消息列表"""
        messages = []
        for msg in conversation.messages:
            if msg.role == MessageRole.USER:
                messages.append({"role": "user", "content": msg.content})
            elif msg.role == MessageRole.ASSISTANT:
                entry = {"role": "assistant"}
                if msg.content:
                    entry["content"] = msg.content
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False)
                            }
                        }
                        for tc in msg.tool_calls
                    ]
                    if not entry.get("content"):
                        entry["content"] = ""
                messages.append(entry)
            elif msg.role == MessageRole.TOOL:
                # 工具结果消息
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_calls[0].id if msg.tool_calls else "",
                    "content": msg.content
                })
        return messages

    async def run_agent_loop(
        self,
        user_message: str,
        conversation_id: Optional[str] = None
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

        # 添加用户消息
        user_msg = Message(role=MessageRole.USER, content=user_message)
        conversation.messages.append(user_msg)

        # 如果是第一条消息，设置对话标题
        if len(conversation.messages) == 1:
            conversation.title = user_message[:50]

        # 发送对话 ID
        yield {
            "event": SSEEventType.CONTENT,
            "data": json.dumps({
                "conversation_id": conversation.id,
                "type": "conversation_info"
            }, ensure_ascii=False)
        }

        iteration = 0
        completed = False
        limit_notice = ""
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

            # 调用 LLM
            messages = self._build_messages(conversation)
            llm_result = await chat_completion(messages, use_tools=True)

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

            else:
                # 有工具调用
                tool_call_objects = []
                for tc_data in tool_calls_data:
                    tc = ToolCall(
                        id=tc_data["id"],
                        name=tc_data["name"],
                        arguments=tc_data.get("arguments", {}),
                        status=ToolCallStatus.RUNNING
                    )
                    tool_call_objects.append(tc)

                # 记录 assistant 消息（包含工具调用）
                assistant_msg = Message(
                    role=MessageRole.ASSISTANT,
                    content=content or "",
                    tool_calls=tool_call_objects
                )
                conversation.messages.append(assistant_msg)

                # 如果有文本内容，先输出
                if content:
                    yield {
                        "event": SSEEventType.CONTENT,
                        "data": json.dumps({
                            "content": content,
                            "type": "intermediate"
                        }, ensure_ascii=False)
                    }

                # 逐个执行工具调用
                for tc in tool_call_objects:
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

                    # 执行工具（传递 conversation_id 实现会话隔离）
                    try:
                        result = await execute_tool(tc.name, tc.arguments, conversation_id=conversation.id)
                        tc.result = result
                        tc.status = ToolCallStatus.COMPLETED
                    except Exception as e:
                        result = f"工具执行失败: {str(e)}"
                        tc.result = result
                        tc.status = ToolCallStatus.FAILED

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
                        tool_calls=[ToolCall(id=tc.id, name=tc.name)]
                    )
                    conversation.messages.append(tool_msg)

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
