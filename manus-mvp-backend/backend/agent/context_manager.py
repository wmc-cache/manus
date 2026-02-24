"""
Context Engineering for Agent Loop.

Key improvements over original:
1. KV-cache friendly compression: only append/trim at tail, never mutate prefix
2. Error memory retention: failed tool calls are preserved with higher priority
3. Enhanced todo.md attention manipulation: structured injection at optimal position
4. Recoverable compression with file-backed externalization
5. Token-aware budgeting with configurable limits
"""
import json
import logging
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.schemas import Conversation, Message, MessageRole, ToolCallStatus
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


# ---------------------------------------------------------------------------
# Masking helpers – used to produce deterministic summaries for externalized
# content so that the LLM can decide whether to re-read the full file.
# ---------------------------------------------------------------------------

def _stable_hash(text: str) -> str:
    """Return a short deterministic hash for cache-key purposes."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


class ContextManager:
    """Builds LLM messages with KV-cache friendly, recoverable compression."""

    def __init__(
        self,
        max_context_messages: int,
        max_recent_message_chars: int,
        max_old_message_chars: int,
        max_plan_chars: int = 3000,
        externalize_threshold_chars: int = 1000,
        message_externalize_threshold_chars: int = 6000,
        summary_chars: int = 600,
        error_retention_window: int = 10,
        max_error_chars: int = 2000,
    ):
        self.max_context_messages = max_context_messages
        self.max_recent_message_chars = max_recent_message_chars
        self.max_old_message_chars = max_old_message_chars
        self.max_plan_chars = max_plan_chars
        self.externalize_threshold_chars = externalize_threshold_chars
        self.message_externalize_threshold_chars = message_externalize_threshold_chars
        self.summary_chars = summary_chars
        # NEW: how many recent messages to scan for errors to retain
        self.error_retention_window = error_retention_window
        self.max_error_chars = max_error_chars

    # ---- Text utilities ----

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [内容已截断，省略 {len(text) - max_chars} 字符]"

    @staticmethod
    def _single_line_preview(text: str) -> str:
        return " ".join(text.split())

    # ---- Externalization (file-backed memory) ----

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

        # NEW: Always preserve error messages in full (up to max_error_chars)
        is_error = self._is_error_tool_message(message)
        if is_error:
            return self._clip_text(content, self.max_error_chars)

        if len(content) >= self.externalize_threshold_chars:
            rel_path = self._externalize_tool_observation(conversation, message, content)
            if rel_path:
                summary = self._clip_text(self._single_line_preview(content), self.summary_chars)
                content_hash = _stable_hash(content)
                return (
                    f"工具输出较长（hash: {content_hash}），完整内容已保存到 `{rel_path}`。\n"
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

    # ---- Error detection ----

    @staticmethod
    def _is_error_tool_message(message: Message) -> bool:
        """Check if a tool message represents a failed execution."""
        if message.role != MessageRole.TOOL:
            return False
        for tc in message.tool_calls:
            if tc.status == ToolCallStatus.FAILED:
                return True
        content = (message.content or "").lower()
        error_indicators = ["出错", "失败", "error", "failed", "exception", "traceback"]
        return any(indicator in content for indicator in error_indicators)

    def _collect_error_indices(self, messages: List[Message]) -> set:
        """Collect indices of error messages and their surrounding context."""
        error_indices = set()
        for idx, msg in enumerate(messages):
            if self._is_error_tool_message(msg):
                # Keep the error message itself
                error_indices.add(idx)
                # Keep the preceding assistant message (tool call that caused error)
                if idx > 0 and messages[idx - 1].role == MessageRole.ASSISTANT:
                    error_indices.add(idx - 1)
                # Keep the next assistant message (recovery attempt)
                if idx + 1 < len(messages) and messages[idx + 1].role == MessageRole.ASSISTANT:
                    error_indices.add(idx + 1)
        return error_indices

    # ---- KV-cache friendly compression ----

    def _compress_old_messages(
        self,
        conversation: Conversation,
        messages: List[Message],
        recent_count: int = 12,
    ) -> Tuple[List[Dict[str, Any]], int]:
        """
        Compress messages with KV-cache friendly strategy:
        - Keep system prompt prefix stable (never mutate)
        - Only trim from the middle, keeping recent messages intact
        - Preserve error messages with higher priority
        - Return (compressed_messages, omitted_count)
        """
        total = len(messages)
        if total <= self.max_context_messages:
            # No compression needed
            return self._format_all_messages(conversation, messages, recent_count), 0

        # Identify error messages in the window that would be dropped
        error_indices = self._collect_error_indices(messages)

        # Calculate how many to drop
        to_keep = self.max_context_messages
        recent_start = max(0, total - recent_count)

        # Always keep: first message + recent messages + error messages
        must_keep_indices = set()
        must_keep_indices.add(0)  # First user message
        for i in range(recent_start, total):
            must_keep_indices.add(i)
        # Add error indices from the droppable range
        for idx in error_indices:
            if idx < recent_start:
                must_keep_indices.add(idx)

        # Build the kept indices
        kept_indices = sorted(must_keep_indices)

        # If we still have budget, fill from the middle
        remaining_budget = to_keep - len(kept_indices)
        if remaining_budget > 0:
            candidates = [i for i in range(total) if i not in must_keep_indices]
            # Prefer more recent messages
            candidates.sort(reverse=True)
            for idx in candidates[:remaining_budget]:
                kept_indices.append(idx)
            kept_indices.sort()

        omitted = total - len(kept_indices)
        result = []

        if omitted > 0:
            result.append({
                "role": "system",
                "content": (
                    f"[上下文压缩] 省略了 {omitted} 条较早消息。"
                    "关键错误记录已保留。如需回顾历史，请读取 context_memory/ 目录。"
                ),
            })

        prev_idx = -1
        for idx in kept_indices:
            # Insert gap marker if there's a discontinuity
            if prev_idx >= 0 and idx - prev_idx > 1:
                gap = idx - prev_idx - 1
                result.append({
                    "role": "system",
                    "content": f"[...省略 {gap} 条消息...]",
                })

            msg = messages[idx]
            is_recent = idx >= recent_start
            limit = self.max_recent_message_chars if is_recent else self.max_old_message_chars
            allow_externalize = not is_recent

            formatted = self._format_single_message(
                conversation, msg, limit, allow_externalize,
                is_error=idx in error_indices,
            )
            if formatted:
                result.append(formatted)
            prev_idx = idx

        return result, omitted

    def _format_single_message(
        self,
        conversation: Conversation,
        msg: Message,
        limit: int,
        allow_externalize: bool,
        is_error: bool = False,
    ) -> Optional[Dict[str, Any]]:
        """Format a single message for LLM input."""
        if msg.role == MessageRole.USER:
            return {
                "role": "user",
                "content": self._build_text_content(
                    conversation=conversation,
                    message=msg,
                    limit=limit,
                    allow_externalize=allow_externalize,
                ),
            }

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
            return entry

        if msg.role == MessageRole.TOOL:
            # For error messages, use higher char limit
            effective_limit = self.max_error_chars if is_error else limit
            return {
                "role": "tool",
                "tool_call_id": msg.tool_calls[0].id if msg.tool_calls else "",
                "content": self._build_tool_content(conversation, msg, effective_limit),
            }

        return None

    def _format_all_messages(
        self,
        conversation: Conversation,
        messages: List[Message],
        recent_count: int = 12,
    ) -> List[Dict[str, Any]]:
        """Format all messages without compression."""
        result = []
        error_indices = self._collect_error_indices(messages)
        recent_start = max(0, len(messages) - recent_count)

        for idx, msg in enumerate(messages):
            is_recent = idx >= recent_start
            limit = self.max_recent_message_chars if is_recent else self.max_old_message_chars
            allow_externalize = not is_recent

            formatted = self._format_single_message(
                conversation, msg, limit, allow_externalize,
                is_error=idx in error_indices,
            )
            if formatted:
                result.append(formatted)
        return result

    # ---- Enhanced todo.md injection ----

    def _build_plan_injection(self, plan_markdown: str) -> Dict[str, Any]:
        """
        Build the plan injection message.
        Placed at the END of context to maximize attention (recency bias).
        Includes structured markers for the LLM to parse.
        """
        return {
            "role": "system",
            "content": (
                "=== 当前任务计划 (todo.md) ===\n"
                "你必须严格按照以下计划执行，优先推进当前标记为 [-] 的阶段。\n"
                "完成当前阶段后，更新计划状态并推进到下一阶段。\n"
                "如果发现计划需要调整，先说明原因再修改。\n\n"
                f"{self._clip_text(plan_markdown, self.max_plan_chars)}\n"
                "=== 计划结束 ==="
            ),
        }

    # ---- Error summary injection ----

    def _build_error_summary(self, messages: List[Message]) -> Optional[Dict[str, Any]]:
        """
        Summarize recent errors into a compact injection.
        This helps the LLM avoid repeating the same mistakes.
        """
        recent = messages[-self.error_retention_window:] if len(messages) > self.error_retention_window else messages
        errors = []
        for msg in recent:
            if self._is_error_tool_message(msg):
                tool_name = ""
                for tc in msg.tool_calls:
                    if tc.status == ToolCallStatus.FAILED:
                        tool_name = tc.name
                        break
                error_preview = self._clip_text(msg.content or "", 200)
                errors.append(f"- `{tool_name}`: {error_preview}")

        if not errors:
            return None

        return {
            "role": "system",
            "content": (
                "[错误记忆] 近期工具调用失败记录（请避免重复相同错误）：\n"
                + "\n".join(errors[-5:])  # Keep at most 5 recent errors
            ),
        }

    # ---- Main build method ----

    def build_messages(
        self,
        conversation: Conversation,
        plan_markdown: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Builds LLM input messages with KV-cache friendly compression.

        Message structure (optimized for attention):
        1. [System] Compression notice (if any)
        2. [History] Compressed old messages with error retention
        3. [Recent] Full recent messages
        4. [System] Error summary (if any recent errors)
        5. [System] Plan injection (todo.md) - at the END for maximum attention
        """
        messages: List[Dict[str, Any]] = []
        all_msgs = conversation.messages

        # Apply KV-cache friendly compression
        compressed, omitted = self._compress_old_messages(
            conversation, all_msgs, recent_count=12,
        )
        messages.extend(compressed)

        # Inject error summary before plan (if there are recent errors)
        error_summary = self._build_error_summary(all_msgs)
        if error_summary:
            messages.append(error_summary)

        # Inject plan at the END for maximum attention (recency bias)
        if plan_markdown:
            messages.append(self._build_plan_injection(plan_markdown))

        return messages


class ToolStateMachine:
    """Phase-aware tool gating policy with enhanced state transitions."""

    PHASE_TOOL_POLICIES = {
        1: {
            "web_search",
            "wide_research",
            "spawn_sub_agents",
            "browser_navigate",
            "browser_screenshot",
            "browser_get_content",
            "browser_click",
            "browser_input",
            "browser_scroll",
            "read_file",
            "write_file",
            "edit_file",
        },
        2: {
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
            "list_files",
            "data_analysis",
        },
        3: {
            "web_search",
            "browser_navigate",
            "browser_get_content",
            "read_file",
            "write_file",
            "edit_file",
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
            browser_bias = {
                "web_search", "browser_navigate", "browser_get_content",
                "browser_screenshot", "browser_click", "browser_input",
                "browser_scroll", "read_file",
            }
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
        error_retention_window=_read_positive_int_env("MANUS_ERROR_RETENTION_WINDOW", 10),
        max_error_chars=_read_positive_int_env("MANUS_MAX_ERROR_CHARS", 2000),
    )
