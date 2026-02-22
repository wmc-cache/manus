"""Context engineering helpers for Agent loop."""
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence

from models.schemas import Conversation, Message, MessageRole
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


def _read_bool_env(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


class ContextManager:
    """Builds LLM messages with recoverable compression."""

    def __init__(
        self,
        max_context_messages: int,
        max_recent_message_chars: int,
        max_old_message_chars: int,
        max_plan_chars: int = 3000,
        externalize_threshold_chars: int = 1000,
        message_externalize_threshold_chars: int = 6000,
        summary_chars: int = 600,
    ):
        self.max_context_messages = max_context_messages
        self.max_recent_message_chars = max_recent_message_chars
        self.max_old_message_chars = max_old_message_chars
        self.max_plan_chars = max_plan_chars
        self.externalize_threshold_chars = externalize_threshold_chars
        self.message_externalize_threshold_chars = message_externalize_threshold_chars
        self.summary_chars = summary_chars

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [内容已截断，省略 {len(text) - max_chars} 字符]"

    @staticmethod
    def _single_line_preview(text: str) -> str:
        return " ".join(text.split())

    def _externalize_tool_observation(
        self,
        conversation: Conversation,
        message: Message,
        content: str,
    ) -> Optional[str]:
        if not conversation.id:
            return None

        workspace_root = Path(get_workspace_root(conversation.id))
        memory_dir = workspace_root / "context_memory" / "tool_observations"
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_path = memory_dir / f"{message.id}.txt"

        try:
            if not memory_path.exists():
                memory_path.write_text(content, encoding="utf-8")
            return os.path.relpath(str(memory_path), str(workspace_root))
        except Exception as exc:
            logger.warning("Failed to externalize tool observation %s: %s", message.id, exc)
            return None

    def _build_tool_content(self, conversation: Conversation, message: Message, limit: int) -> str:
        content = message.content or ""
        if len(content) >= self.externalize_threshold_chars:
            rel_path = self._externalize_tool_observation(conversation, message, content)
            if rel_path:
                summary = self._clip_text(self._single_line_preview(content), self.summary_chars)
                return (
                    f"工具输出较长，完整内容已保存到 `{rel_path}`。\n"
                    f"摘要: {summary}\n"
                    "如需完整内容，请调用 `read_file` 读取该文件。"
                )

        if len(content) <= limit:
            return content
        return self._clip_text(content, limit)

    def _externalize_text_message(
        self,
        conversation: Conversation,
        message: Message,
        content: str,
    ) -> Optional[str]:
        if not conversation.id:
            return None

        workspace_root = Path(get_workspace_root(conversation.id))
        role_dir = "user" if message.role == MessageRole.USER else "assistant"
        memory_dir = workspace_root / "context_memory" / "messages" / role_dir
        memory_dir.mkdir(parents=True, exist_ok=True)
        memory_path = memory_dir / f"{message.id}.txt"

        try:
            if not memory_path.exists():
                memory_path.write_text(content, encoding="utf-8")
            return os.path.relpath(str(memory_path), str(workspace_root))
        except Exception as exc:
            logger.warning("Failed to externalize message %s: %s", message.id, exc)
            return None

    def _build_text_content(
        self,
        conversation: Conversation,
        message: Message,
        limit: int,
        allow_externalize: bool,
    ) -> str:
        content = message.content or ""
        if (
            allow_externalize
            and len(content) >= self.message_externalize_threshold_chars
        ):
            rel_path = self._externalize_text_message(conversation, message, content)
            if rel_path:
                summary = self._clip_text(self._single_line_preview(content), self.summary_chars)
                label = "历史用户消息" if message.role == MessageRole.USER else "历史助手消息"
                return (
                    f"{label}较长，完整内容已保存到 `{rel_path}`。\n"
                    f"摘要: {summary}\n"
                    "如需完整内容，请调用 `read_file` 读取该文件。"
                )

        if len(content) <= limit:
            return content
        return self._clip_text(content, limit)

    def build_messages(
        self,
        conversation: Conversation,
        plan_markdown: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """Builds LLM input messages with context compression policy."""
        messages: List[Dict[str, Any]] = []
        all_msgs = conversation.messages
        sliced = (
            all_msgs[-self.max_context_messages:]
            if len(all_msgs) > self.max_context_messages
            else all_msgs
        )

        omitted = len(all_msgs) - len(sliced)
        if omitted > 0:
            messages.append({
                "role": "system",
                "content": f"上下文已压缩：省略了更早的 {omitted} 条消息，请聚焦当前任务。",
            })

        recent_start = max(0, len(sliced) - 12)
        for idx, msg in enumerate(sliced):
            limit = self.max_recent_message_chars if idx >= recent_start else self.max_old_message_chars
            allow_externalize = idx < recent_start

            if msg.role == MessageRole.USER:
                messages.append({
                    "role": "user",
                    "content": self._build_text_content(
                        conversation=conversation,
                        message=msg,
                        limit=limit,
                        allow_externalize=allow_externalize,
                    ),
                })
                continue

            if msg.role == MessageRole.ASSISTANT:
                entry: Dict[str, Any] = {"role": "assistant"}
                if msg.content:
                    entry["content"] = self._build_text_content(
                        conversation=conversation,
                        message=msg,
                        limit=limit,
                        allow_externalize=allow_externalize,
                    )
                if msg.tool_calls:
                    entry["tool_calls"] = [
                        {
                            "id": tc.id,
                            "type": "function",
                            "function": {
                                "name": tc.name,
                                "arguments": json.dumps(tc.arguments, ensure_ascii=False),
                            },
                        }
                        for tc in msg.tool_calls
                    ]
                    if not entry.get("content"):
                        entry["content"] = ""
                messages.append(entry)
                continue

            if msg.role == MessageRole.TOOL:
                messages.append({
                    "role": "tool",
                    "tool_call_id": msg.tool_calls[0].id if msg.tool_calls else "",
                    "content": self._build_tool_content(conversation, msg, limit),
                })

        if plan_markdown:
            messages.append({
                "role": "system",
                "content": (
                    "你必须严格对齐当前任务计划（todo.md）执行，优先推进当前阶段：\n"
                    f"{self._clip_text(plan_markdown, self.max_plan_chars)}"
                ),
            })

        return messages


class ToolStateMachine:
    """Simple phase-aware tool gating policy."""

    PHASE_TOOL_POLICIES = {
        1: {
            "web_search",
            "wide_research",
            "browser_navigate",
            "browser_screenshot",
            "browser_get_content",
            "read_file",
            "write_file",
        },
        3: {
            "web_search",
            "browser_navigate",
            "browser_get_content",
            "read_file",
            "write_file",
        },
    }

    def __init__(self, enabled: Optional[bool] = None):
        if enabled is None:
            enabled = _read_bool_env("MANUS_ENABLE_TOOL_STATE_MACHINE", True)
        self.enabled = bool(enabled)

    @staticmethod
    def _last_tool_name(conversation: Conversation) -> str:
        for msg in reversed(conversation.messages):
            if msg.role != MessageRole.TOOL or not msg.tool_calls:
                continue
            name = (msg.tool_calls[0].name or "").strip()
            if name:
                return name
        return ""

    def get_allowed_tools(self, conversation: Conversation, candidate_tools: Sequence[str]) -> List[str]:
        allowed = [name for name in candidate_tools if isinstance(name, str) and name]
        if not self.enabled or not allowed:
            return allowed

        phase_id = conversation.plan.current_phase_id if conversation.plan else None
        phase_policy = self.PHASE_TOOL_POLICIES.get(phase_id)
        if phase_policy:
            narrowed = [tool for tool in allowed if tool in phase_policy]
            if narrowed:
                allowed = narrowed

        last_tool = self._last_tool_name(conversation)
        if phase_id == 3:
            narrowed = [tool for tool in allowed if tool not in {"shell_exec", "execute_code"}]
            if narrowed:
                allowed = narrowed
        elif phase_id == 1 and last_tool.startswith("browser_"):
            browser_bias = {"web_search", "browser_navigate", "browser_get_content", "browser_screenshot", "read_file"}
            narrowed = [tool for tool in allowed if tool in browser_bias]
            if narrowed:
                allowed = narrowed

        return allowed


def build_default_context_manager(
    max_context_messages: int,
    max_recent_message_chars: int,
    max_old_message_chars: int,
) -> ContextManager:
    return ContextManager(
        max_context_messages=max_context_messages,
        max_recent_message_chars=max_recent_message_chars,
        max_old_message_chars=max_old_message_chars,
        max_plan_chars=_read_positive_int_env("MANUS_CONTEXT_PLAN_MAX_CHARS", 3000),
        externalize_threshold_chars=_read_positive_int_env("MANUS_CONTEXT_EXTERNALIZE_THRESHOLD", 1000),
        message_externalize_threshold_chars=_read_positive_int_env(
            "MANUS_CONTEXT_MESSAGE_EXTERNALIZE_THRESHOLD", 6000
        ),
        summary_chars=_read_positive_int_env("MANUS_CONTEXT_SUMMARY_CHARS", 600),
    )
