"""
工具执行编排 — 从 agent/core.py 中抽取的工具调用分类、并行/串行执行逻辑。

职责：
- 将工具调用分为并行安全组和串行组
- 并行执行只读工具
- 串行执行有副作用工具
- 工具循环检测
- 心跳进度通知
"""

import asyncio
import hashlib
import json
import logging
from collections import Counter
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Tuple

from config.settings import settings
from models.schemas import ToolCall, ToolCallStatus, SSEEventType


logger = logging.getLogger(__name__)


class ToolExecutor:
    """编排工具调用的执行策略。"""

    def __init__(
        self,
        execute_tool_fn: Callable[..., Coroutine],
        parallel_safe_tools: Optional[Set[str]] = None,
    ):
        """
        Args:
            execute_tool_fn: 实际执行工具的异步函数，签名为
                             async (name, arguments, conversation_id) -> str
            parallel_safe_tools: 可安全并行执行的工具名称集合
        """
        self._execute_tool = execute_tool_fn
        self._parallel_safe_tools = parallel_safe_tools or settings.agent.parallel_safe_tools

    # ---- 工具分类 ----

    def classify_tool_calls(
        self,
        tool_calls: List[ToolCall],
    ) -> Tuple[List[ToolCall], List[ToolCall]]:
        """将工具调用分为可并行组和必须串行组。"""
        parallel_batch: List[ToolCall] = []
        serial_queue: List[ToolCall] = []
        for tc in tool_calls:
            if tc.name in self._parallel_safe_tools:
                parallel_batch.append(tc)
            else:
                serial_queue.append(tc)
        return parallel_batch, serial_queue

    # ---- 并行执行 ----

    async def execute_parallel(
        self,
        tool_calls: List[ToolCall],
        conversation_id: str,
        parse_errors: Dict[str, str],
        parse_previews: Dict[str, str],
    ) -> List[Tuple[ToolCall, str, ToolCallStatus]]:
        """并行执行一批安全工具，返回 (tool_call, result, status) 列表。"""
        if not tool_calls:
            return []

        async def _exec_one(tc: ToolCall) -> Tuple[ToolCall, str, ToolCallStatus]:
            parse_error = parse_errors.get(tc.id)
            if parse_error:
                preview = parse_previews.get(tc.id, "")
                result = (
                    f"工具执行失败: 模型生成的 `{tc.name}` 参数不是合法 JSON。"
                    f"原因: {parse_error}"
                )
                if preview:
                    result += f"\n参数片段: {preview}"
                return tc, result, ToolCallStatus.FAILED
            try:
                result = await self._execute_tool(
                    tc.name, tc.arguments, conversation_id=conversation_id
                )
                return tc, result, ToolCallStatus.COMPLETED
            except Exception as e:
                return tc, f"工具执行失败: {str(e)}", ToolCallStatus.FAILED

        tasks = [_exec_one(tc) for tc in tool_calls]
        raw_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[Tuple[ToolCall, str, ToolCallStatus]] = []
        for r in raw_results:
            if isinstance(r, Exception):
                logger.warning("Parallel tool execution raised: %s", r)
                continue
            results.append(r)
        return results

    # ---- 串行执行（带心跳） ----

    async def execute_serial_one(
        self,
        tc: ToolCall,
        conversation_id: str,
        parse_errors: Dict[str, str],
        parse_previews: Dict[str, str],
        heartbeat_seconds: float = 0,
    ):
        """
        串行执行单个工具，支持心跳。

        Returns:
            (result_text, status, heartbeat_events)
            heartbeat_events 是执行期间产生的心跳 SSE 事件列表
        """
        heartbeat_events: List[Dict[str, Any]] = []

        parse_error = parse_errors.get(tc.id)
        if parse_error:
            preview = parse_previews.get(tc.id, "")
            result = (
                f"工具执行失败: 模型生成的 `{tc.name}` 参数不是合法 JSON。"
                f"原因: {parse_error}"
                "。这通常是因为内容过长导致参数被截断。"
            )
            if preview:
                result += f"\n参数片段: {preview}"
            return result, ToolCallStatus.FAILED, heartbeat_events

        try:
            if heartbeat_seconds <= 0:
                result = await self._execute_tool(
                    tc.name, tc.arguments, conversation_id=conversation_id
                )
            else:
                tool_task = asyncio.create_task(
                    self._execute_tool(tc.name, tc.arguments, conversation_id=conversation_id)
                )
                while True:
                    try:
                        result = await asyncio.wait_for(
                            asyncio.shield(tool_task),
                            timeout=heartbeat_seconds,
                        )
                        break
                    except asyncio.TimeoutError:
                        heartbeat_events.append({
                            "event": SSEEventType.THINKING,
                            "data": json.dumps({
                                "status": "waiting_tool",
                                "tool_name": tc.name,
                                "message": f"工具 `{tc.name}` 执行中…",
                            }, ensure_ascii=False),
                        })
            return result, ToolCallStatus.COMPLETED, heartbeat_events
        except Exception as e:
            return f"工具执行失败: {str(e)}", ToolCallStatus.FAILED, heartbeat_events

    # ---- 工具循环检测 ----

    @staticmethod
    def build_tool_signature(name: str, arguments: dict) -> str:
        """构建工具调用签名用于循环检测。"""
        try:
            canonical = json.dumps(arguments, sort_keys=True, ensure_ascii=False)
        except (TypeError, ValueError):
            canonical = str(arguments)
        raw = f"{name}:{canonical}"
        return hashlib.md5(raw.encode("utf-8")).hexdigest()

    @staticmethod
    def is_repeated_signature(
        recent_signatures: List[str],
        new_signature: str,
        threshold: int = 0,
    ) -> bool:
        """检测工具签名是否在最近窗口中重复超过阈值。"""
        if threshold <= 0:
            threshold = settings.agent.tool_loop_repeat_threshold
        count = Counter(recent_signatures).get(new_signature, 0)
        return count >= threshold
