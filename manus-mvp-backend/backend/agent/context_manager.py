"""
智能体循环的上下文工程 
KV 缓存友好的压缩策略：仅在尾部追加/裁剪，前缀前缀永不修改
错误记忆保留：失败的工具调用以更高优先级保存
增强的 todo.md 注意力操控：在最优位置进行结构化注入
可恢复压缩，支持文件落盘外化存储
可配置上限的 Token 感知预算管理
基于计划阶段能力的动态工具门控（而非硬编码阶段 ID）
推理强度注入，实现自适应思考深度
已压缩旧消息的紧凑历史标记
"""
import base64
import json
import logging
import os
import hashlib
from pathlib import Path
from typing import Any, Dict, List, Optional, Sequence, Tuple

from models.schemas import Conversation, Message, MessageRole, ToolCallStatus
from sandbox.filesystem import get_workspace_root
from llm.tokenizer import (
    count_tokens,
    count_messages_tokens,
    estimate_remaining_budget,
    truncate_to_token_budget,
)


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
# Masking helpers
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
        self.error_retention_window = error_retention_window
        self.max_error_chars = max_error_chars

    # ---- Text utilities ----

    @staticmethod
    def _clip_text(text: str, max_chars: int) -> str:
        """Clip text by character count (fast path)."""
        if len(text) <= max_chars:
            return text
        return text[:max_chars] + f"\n... [内容已截断，省略 {len(text) - max_chars} 字符]"

    @staticmethod
    def _clip_text_by_tokens(text: str, max_tokens: int) -> str:
        """[新增] Clip text by token count (precise path)."""
        return truncate_to_token_budget(text, max_tokens)

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

        # Always preserve error messages in full (up to max_error_chars)
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
                error_indices.add(idx)
                if idx > 0 and messages[idx - 1].role == MessageRole.ASSISTANT:
                    error_indices.add(idx - 1)
                if idx + 1 < len(messages) and messages[idx + 1].role == MessageRole.ASSISTANT:
                    error_indices.add(idx + 1)
        return error_indices

    # ---- Compacted history markers (new) ----

    def _build_compacted_history_marker(
        self,
        messages: List[Message],
        start_idx: int,
        end_idx: int,
    ) -> str:
        """
        Build a concise summary of compacted messages, similar to Manus 1.6 Max's
        <compacted_history> markers. Instead of just saying "N messages omitted",
        provide a brief description of what tools were used.
        """
        tool_actions = []
        for i in range(start_idx, min(end_idx, len(messages))):
            msg = messages[i]
            if msg.role == MessageRole.ASSISTANT and msg.tool_calls:
                for tc in msg.tool_calls:
                    brief = f"{tc.name}"
                    if tc.arguments:
                        # Extract a key argument for context
                        args = tc.arguments
                        if isinstance(args, dict):
                            for key in ["command", "path", "query", "url", "code"]:
                                if key in args:
                                    val = str(args[key])[:60]
                                    brief += f"({key}={val})"
                                    break
                    tool_actions.append(brief)

        count = end_idx - start_idx
        if tool_actions:
            actions_str = ", ".join(tool_actions[:8])
            if len(tool_actions) > 8:
                actions_str += f" ... 等 {len(tool_actions)} 个操作"
            return (
                f"<compacted_history>\n"
                f"省略了 {count} 条消息。主要操作: {actions_str}\n"
                f"关键错误记录已保留。如需回顾历史，请读取 context_memory/ 目录。\n"
                f"</compacted_history>"
            )
        return f"[上下文压缩] 省略了 {count} 条较早消息。如需回顾历史，请读取 context_memory/ 目录。"

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
        - Use compacted_history markers for better context
        """
        total = len(messages)
        if total <= self.max_context_messages:
            return self._format_all_messages(conversation, messages, recent_count), 0

        error_indices = self._collect_error_indices(messages)

        to_keep = self.max_context_messages
        recent_start = max(0, total - recent_count)

        must_keep_indices = set()
        must_keep_indices.add(0)  # First user message
        for i in range(recent_start, total):
            must_keep_indices.add(i)
        for idx in error_indices:
            if idx < recent_start:
                must_keep_indices.add(idx)

        kept_indices = sorted(must_keep_indices)

        remaining_budget = to_keep - len(kept_indices)
        if remaining_budget > 0:
            candidates = [i for i in range(total) if i not in must_keep_indices]
            candidates.sort(reverse=True)
            for idx in candidates[:remaining_budget]:
                kept_indices.append(idx)
            kept_indices.sort()

        omitted = total - len(kept_indices)
        result = []

        if omitted > 0:
            # Build compacted history marker with tool action summaries
            dropped_start = 1  # After first message
            dropped_end = recent_start
            marker = self._build_compacted_history_marker(messages, dropped_start, dropped_end)
            result.append({
                "role": "system",
                "content": marker,
            })

        prev_idx = -1
        for idx in kept_indices:
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
            text_content = self._build_text_content(
                conversation=conversation,
                message=msg,
                limit=limit,
                allow_externalize=allow_externalize,
            )
            images = getattr(msg, "images", None) or []
            vision_parts = self._build_vision_parts(images, conversation)
            if vision_parts:
                content_parts = [{"type": "text", "text": text_content}] + vision_parts
                return {"role": "user", "content": content_parts}
            return {
                "role": "user",
                "content": text_content,
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
            effective_limit = self.max_error_chars if is_error else limit
            return {
                "role": "tool",
                "tool_call_id": msg.tool_calls[0].id if msg.tool_calls else "",
                "content": self._build_tool_content(conversation, msg, effective_limit),
            }

        return None

    def _build_vision_parts(
        self,
        images: list,
        conversation,
    ) -> list:
        """将上传图片编码为 LLM vision content parts。"""
        parts = []
        conversation_id = getattr(conversation, "id", None) or "_default"
        workspace_root = Path(get_workspace_root(conversation_id)).resolve()
        for image in images[:6]:
            path = str(getattr(image, "path", "") or "").strip()
            mime_type = str(getattr(image, "mime_type", "") or "image/png").strip()
            if not path:
                continue
            file_path = (workspace_root / path).resolve()
            if workspace_root not in file_path.parents and file_path != workspace_root:
                continue
            try:
                raw = file_path.read_bytes()
                b64 = base64.b64encode(raw).decode("ascii")
                parts.append({
                    "type": "image_url",
                    "image_url": {
                        "url": f"data:{mime_type};base64,{b64}",
                    },
                })
            except Exception:
                continue
        return parts

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
        """
        return {
            "role": "system",
            "content": (
                "=== 当前任务计划 (todo.md) ===\n"
                "请将以下计划作为执行主线，优先推进当前标记为 [-] 的阶段。\n"
                "完成当前阶段后，更新计划状态并推进到下一阶段。\n"
                "如果发现计划不再适配当前上下文，可先说明原因再调整计划。\n"
                "严禁跳过阶段或回退阶段；如需调整，应修订整个计划。\n\n"
                f"{self._clip_text(plan_markdown, self.max_plan_chars)}\n"
                "=== 计划结束 ==="
            ),
        }

    # ---- Reasoning effort injection (new) ----

    def _build_reasoning_effort_injection(
        self,
        conversation: Conversation,
    ) -> Optional[Dict[str, Any]]:
        """
        Inject reasoning effort guidance based on task complexity.
        Similar to Manus 1.6 Max's <reasoning_effort> mechanism.
        """
        msg_count = len(conversation.messages)
        plan = conversation.plan

        # Determine effort level
        if plan and plan.phases:
            total_phases = len(plan.phases)
            if total_phases >= 6:
                effort = "high"
            elif total_phases >= 4:
                effort = "medium"
            else:
                effort = "low"
        elif msg_count > 20:
            effort = "high"
        elif msg_count > 8:
            effort = "medium"
        else:
            effort = "low"

        effort_instructions = {
            "high": (
                "[推理深度: 高] 当前任务较为复杂，请进行深入思考。"
                "在选择工具前，仔细分析当前状态、已有信息和可能的方案。"
                "考虑边界情况和潜在问题。"
            ),
            "medium": (
                "[推理深度: 中] 当前任务中等复杂度。"
                "请保持适度的思考深度，平衡效率和质量。"
            ),
            "low": (
                "[推理深度: 低] 当前任务较为简单。"
                "快速执行即可，无需过度思考。"
            ),
        }

        return {
            "role": "system",
            "content": effort_instructions.get(effort, effort_instructions["medium"]),
        }

    # ---- Error summary injection ----

    def _build_error_summary(self, messages: List[Message]) -> Optional[Dict[str, Any]]:
        """Summarize recent errors into a compact injection."""
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
                + "\n".join(errors[-5:])
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
        1. [System] Compression notice / compacted history (if any)
        2. [History] Compressed old messages with error retention
        3. [Recent] Full recent messages
        4. [System] Error summary (if any recent errors)
        5. [System] Reasoning effort guidance
        6. [System] Plan injection (todo.md) - at the END for maximum attention
        """
        messages: List[Dict[str, Any]] = []
        all_msgs = conversation.messages

        # Apply KV-cache friendly compression
        compressed, omitted = self._compress_old_messages(
            conversation, all_msgs, recent_count=12,
        )
        messages.extend(compressed)

        # Inject error summary before plan
        error_summary = self._build_error_summary(all_msgs)
        if error_summary:
            messages.append(error_summary)

        # Inject reasoning effort guidance
        reasoning = self._build_reasoning_effort_injection(conversation)
        if reasoning:
            messages.append(reasoning)

        # Inject plan at the END for maximum attention (recency bias)
        if plan_markdown:
            messages.append(self._build_plan_injection(plan_markdown))

        # [P1优化] Token 预算检查 — 如果超出预算，从中间裁剪最旧的消息
        max_context_tokens = int(os.environ.get("MANUS_MAX_CONTEXT_TOKENS", "60000"))
        reserved_tokens = int(os.environ.get("MANUS_RESERVED_RESPONSE_TOKENS", "4000"))
        total_tokens = count_messages_tokens(messages)
        budget = max_context_tokens - reserved_tokens

        if total_tokens > budget and len(messages) > 4:
            logger.warning(
                "Context tokens (%d) exceed budget (%d), trimming oldest messages",
                total_tokens, budget,
            )
            # 保留第一条和最后 N 条，从中间删除
            while total_tokens > budget and len(messages) > 4:
                # 删除第二条（第一条是用户原始消息或压缩标记，保留）
                removed = messages.pop(1)
                total_tokens = count_messages_tokens(messages)

            logger.info("After trimming: %d tokens, %d messages", total_tokens, len(messages))

        return messages


class ToolStateMachine:
    """
    Phase-aware tool gating policy with enhanced state transitions.

    Enhanced: Now supports dynamic capability-based gating in addition to
    hardcoded phase policies. When a plan phase has 'capabilities' metadata,
    the tool set is dynamically adjusted.
    """

    # Capability-to-tools mapping (similar to Manus 1.6 Max phase capabilities)
    CAPABILITY_TOOLS = {
        "deep_research": {
            "web_search", "wide_research", "spawn_sub_agents",
            "browser_navigate", "browser_get_content", "browser_screenshot",
            "browser_click", "browser_input", "browser_scroll",
            "read_file", "write_file", "edit_file", "find_files", "grep_files",
            "expose_port",
        },
        "data_analysis": {
            "data_analysis", "execute_code", "shell_exec",
            "read_file", "write_file", "edit_file", "list_files",
            "find_files", "grep_files", "expose_port",
        },
        "web_development": {
            "shell_exec", "execute_code",
            "read_file", "write_file", "edit_file", "append_file",
            "list_files", "find_files", "grep_files",
            "browser_navigate", "browser_get_content", "browser_screenshot",
            "expose_port",
        },
        "technical_writing": {
            "web_search", "read_file", "write_file", "edit_file",
            "append_file", "list_files", "find_files", "grep_files",
        },
        "creative_writing": {
            "web_search", "read_file", "write_file", "edit_file",
            "append_file",
        },
        "parallel_processing": {
            "wide_research", "spawn_sub_agents",
            "web_search", "read_file", "write_file",
        },
    }

    # Fallback: hardcoded phase policies (used when no capabilities are specified)
    PHASE_TOOL_POLICIES = {
        1: {
            "web_search", "wide_research", "spawn_sub_agents",
            "browser_navigate", "browser_screenshot", "browser_get_content",
            "browser_click", "browser_input", "browser_scroll",
            "read_file", "write_file", "edit_file",
            "find_files", "grep_files", "expose_port",
        },
        2: {
            "web_search", "wide_research", "spawn_sub_agents",
            "shell_exec", "execute_code",
            "browser_navigate", "browser_screenshot", "browser_get_content",
            "browser_click", "browser_input", "browser_scroll",
            "read_file", "write_file", "edit_file", "append_file",
            "list_files", "data_analysis",
            "find_files", "grep_files", "expose_port",
        },
        3: {
            "web_search",
            "browser_navigate", "browser_get_content",
            "read_file", "write_file", "edit_file", "append_file",
            "find_files", "grep_files", "expose_port",
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

    @staticmethod
    def _latest_user_content(conversation: Conversation) -> str:
        for msg in reversed(conversation.messages):
            if msg.role != MessageRole.USER:
                continue
            text = (msg.content or "").strip()
            if text:
                return text
        return ""

    @classmethod
    def _is_latest_news_query(cls, conversation: Conversation) -> bool:
        text = cls._latest_user_content(conversation).lower()
        if not text:
            return False

        # 时效性资讯查询：优先 web_search，避免默认进入浏览器滚动抓取。
        keyword_hits = [
            "最新", "最近", "今日", "今天", "动态", "新闻", "快讯", "头条",
            "latest", "recent", "today", "news", "headline", "update", "updates",
        ]
        domain_hits = [
            "行业", "人工智能", "科技", "金融", "市场", "政策", "发布",
            "tech", "finance", "market", "policy", "release",
        ]
        has_news_intent = any(token in text for token in keyword_hits)
        has_domain_target = any(token in text for token in domain_hits)
        return has_news_intent or has_domain_target

    def _get_capability_tools(self, conversation: Conversation) -> Optional[set]:
        """
        Get allowed tools based on current phase capabilities.
        Returns None if no capabilities are defined (fall back to phase policy).
        """
        plan = conversation.plan
        if not plan or not plan.phases:
            return None

        current_phase = None
        for phase in plan.phases:
            if phase.id == plan.current_phase_id:
                current_phase = phase
                break

        if not current_phase:
            return None

        # Check if phase has capabilities attribute
        capabilities = getattr(current_phase, 'capabilities', None)
        if not capabilities or not isinstance(capabilities, dict):
            return None

        # Build tool set from capabilities
        tools = set()
        for cap_name, enabled in capabilities.items():
            if enabled and cap_name in self.CAPABILITY_TOOLS:
                tools.update(self.CAPABILITY_TOOLS[cap_name])

        return tools if tools else None

    def get_allowed_tools(self, conversation: Conversation, candidate_tools: Sequence[str]) -> List[str]:
        allowed = [name for name in candidate_tools if isinstance(name, str) and name]
        if not self.enabled or not allowed:
            return allowed

        if self._is_latest_news_query(conversation):
            search_first = {
                "web_search", "wide_research", "spawn_sub_agents",
                "read_file", "write_file", "edit_file", "append_file",
                "find_files", "grep_files", "list_files",
            }
            narrowed = [tool for tool in allowed if tool in search_first]
            if "web_search" in narrowed:
                allowed = narrowed

        # Try capability-based gating first
        cap_tools = self._get_capability_tools(conversation)
        if cap_tools:
            narrowed = [tool for tool in allowed if tool in cap_tools]
            if narrowed:
                return narrowed

        # Fall back to hardcoded phase policies
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
            recovery_bias = {
                "web_search", "wide_research", "spawn_sub_agents",
                "read_file", "write_file", "edit_file",
                "find_files", "grep_files", "list_files",
            }
            narrowed = [tool for tool in allowed if tool in recovery_bias]
            if "web_search" in narrowed:
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
