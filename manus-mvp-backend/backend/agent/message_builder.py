"""
消息构建器 — 从 agent/core.py 中抽取的 LLM 消息列表构建逻辑。

职责：
- 将 Conversation 对象转换为 LLM API 所需的消息列表
- 处理消息截断和上下文窗口管理
- 注入系统提示词和计划上下文
"""

import logging
from typing import Any, Dict, List, Optional

from config.settings import settings
from models.schemas import (
    Conversation,
    Message,
    MessageRole,
    TaskPlan,
    PlanPhaseStatus,
)
from llm.system_prompt import get_system_prompt


logger = logging.getLogger(__name__)


class MessageBuilder:
    """将 Conversation 转换为 LLM API 消息列表。"""

    def __init__(
        self,
        max_context_messages: int = 0,
        max_recent_chars: int = 0,
        max_old_chars: int = 0,
    ):
        self._max_context_messages = max_context_messages or settings.agent.max_context_messages
        self._max_recent_chars = max_recent_chars or settings.agent.max_recent_message_chars
        self._max_old_chars = max_old_chars or settings.agent.max_old_message_chars

    def build_messages(
        self,
        conversation: Conversation,
        extra_system_context: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        构建 LLM 消息列表。

        Args:
            conversation: 当前会话
            extra_system_context: 额外注入到系统提示词的上下文

        Returns:
            LLM API 格式的消息列表
        """
        system_prompt = get_system_prompt()
        if extra_system_context:
            system_prompt += f"\n\n{extra_system_context}"

        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

        # 注入计划上下文
        plan_context = self._build_plan_context(conversation.plan)
        if plan_context:
            messages.append({"role": "system", "content": plan_context})

        # 截断并添加对话历史
        history = conversation.messages[-self._max_context_messages:]
        total = len(history)
        recent_boundary = max(0, total - 6)

        for idx, msg in enumerate(history):
            is_recent = idx >= recent_boundary
            max_chars = self._max_recent_chars if is_recent else self._max_old_chars
            messages.append(self._convert_message(msg, max_chars))

        return messages

    @staticmethod
    def _build_plan_context(plan: Optional[TaskPlan]) -> str:
        """构建计划上下文注入到系统提示词。"""
        if not plan or not plan.phases:
            return ""

        lines = [
            "## 当前任务计划",
            f"目标: {plan.goal}",
            "",
        ]

        status_labels = {
            PlanPhaseStatus.PENDING: "待执行",
            PlanPhaseStatus.RUNNING: "执行中 ←",
            PlanPhaseStatus.COMPLETED: "已完成 ✓",
            PlanPhaseStatus.FAILED: "失败 ✗",
        }

        for phase in plan.phases:
            label = status_labels.get(phase.status, "未知")
            lines.append(f"  {phase.id}. {phase.title} [{label}]")

        current_phase = next(
            (p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING),
            None,
        )
        if current_phase:
            lines.append("")
            lines.append(f"你当前正在执行: 阶段 {current_phase.id} — {current_phase.title}")
            lines.append("请围绕当前阶段目标工作，完成后自然推进到下一阶段。")

        return "\n".join(lines)

    def _convert_message(self, msg: Message, max_chars: int) -> Dict[str, Any]:
        """将 Message 对象转换为 LLM API 格式。"""
        content = msg.content or ""
        if len(content) > max_chars:
            content = content[:max_chars] + f"\n...(已截断，原始长度 {len(msg.content)} 字符)"

        result: Dict[str, Any] = {
            "role": msg.role.value,
            "content": content,
        }

        # 处理用户消息中的图片
        if msg.role == MessageRole.USER and msg.images:
            content_parts = []
            if content:
                content_parts.append({"type": "text", "text": content})
            for image in msg.images:
                if image.data_url:
                    content_parts.append({
                        "type": "image_url",
                        "image_url": {"url": image.data_url},
                    })
                elif image.path:
                    content_parts.append({
                        "type": "text",
                        "text": f"[已上传图片: {image.name or image.path}]",
                    })
            if content_parts:
                result["content"] = content_parts

        # 处理工具调用
        if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
            tool_calls_list = []
            for tc in msg.tool_calls:
                tool_calls_list.append({
                    "id": tc.id,
                    "type": "function",
                    "function": {
                        "name": tc.name,
                        "arguments": tc.arguments if isinstance(tc.arguments, str) else
                            __import__("json").dumps(tc.arguments, ensure_ascii=False),
                    },
                })
            result["tool_calls"] = tool_calls_list

        if msg.role == MessageRole.TOOL and msg.tool_calls:
            tc = msg.tool_calls[0]
            result["tool_call_id"] = tc.id
            result["content"] = content

        return result
