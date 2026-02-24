"""
Enhanced Parallel Processing Module.

Key improvements over original spawn_sub_agents:
1. Independent context isolation per sub-agent (no shared state leakage)
2. Automatic retry with exponential backoff for failed sub-agents
3. Real-time progress tracking with SSE events
4. Structured error recovery and partial result aggregation
5. Resource-aware concurrency control
6. Sub-agent timeout protection
"""
import asyncio
import json
import logging
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import notify_file_change, get_workspace_root

logger = logging.getLogger(__name__)


class SubAgentContext:
    """Isolated context for a single sub-agent execution."""

    def __init__(
        self,
        agent_id: str,
        item: str,
        prompt: str,
        workspace_dir: str,
        conversation_id: Optional[str] = None,
        max_iterations: int = 4,
    ):
        self.agent_id = agent_id
        self.item = item
        self.prompt = prompt
        self.workspace_dir = workspace_dir
        self.conversation_id = conversation_id
        self.max_iterations = max_iterations

        # Independent message history (no shared state)
        self.messages: List[Dict[str, Any]] = []
        self.tool_steps: List[Dict[str, Any]] = []
        self.iterations = 0
        self.status = "pending"
        self.final_answer = ""
        self.error = ""
        self.retry_count = 0
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "item": self.item,
            "status": self.status,
            "iterations": self.iterations,
            "retry_count": self.retry_count,
            "final_answer": self.final_answer,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


class ProgressTracker:
    """Tracks and reports progress of parallel sub-agent execution."""

    def __init__(self, total: int, conversation_id: Optional[str] = None):
        self.total = total
        self.conversation_id = conversation_id
        self.completed = 0
        self.failed = 0
        self.running = 0
        self._lock = asyncio.Lock()

    async def on_start(self, agent_id: str):
        async with self._lock:
            self.running += 1
            await self._emit_progress(f"子代理 {agent_id} 开始执行")

    async def on_complete(self, agent_id: str, success: bool):
        async with self._lock:
            self.running -= 1
            if success:
                self.completed += 1
            else:
                self.failed += 1
            status = "成功" if success else "失败"
            await self._emit_progress(f"子代理 {agent_id} 执行{status}")

    async def on_retry(self, agent_id: str, attempt: int):
        async with self._lock:
            await self._emit_progress(f"子代理 {agent_id} 第 {attempt} 次重试")

    async def _emit_progress(self, message: str):
        try:
            await event_bus.publish(SandboxEvent(
                "sub_agent_progress",
                {
                    "total": self.total,
                    "completed": self.completed,
                    "failed": self.failed,
                    "running": self.running,
                    "message": message,
                    "progress_pct": round(
                        (self.completed + self.failed) / self.total * 100, 1
                    ) if self.total > 0 else 0,
                },
                window_id="terminal_default",
                conversation_id=self.conversation_id,
            ))
        except Exception as exc:
            logger.debug("Failed to emit progress: %s", exc)

    def summary(self) -> str:
        return (
            f"总计: {self.total}, 成功: {self.completed}, "
            f"失败: {self.failed}, 成功率: "
            f"{self.completed / self.total * 100:.0f}%"
            if self.total > 0 else "无任务"
        )


class EnhancedParallelExecutor:
    """Enhanced parallel executor with retry, progress tracking, and error recovery."""

    def __init__(
        self,
        max_concurrency: int = 3,
        max_retries: int = 2,
        retry_base_delay: float = 2.0,
        sub_agent_timeout: float = 120.0,
    ):
        self.max_concurrency = max_concurrency
        self.max_retries = max_retries
        self.retry_base_delay = retry_base_delay
        self.sub_agent_timeout = sub_agent_timeout

    async def execute_parallel(
        self,
        contexts: List[SubAgentContext],
        run_func: Callable,
        conversation_id: Optional[str] = None,
    ) -> List[SubAgentContext]:
        """
        Execute sub-agents in parallel with retry and progress tracking.

        Args:
            contexts: List of SubAgentContext instances
            run_func: async function(context) -> SubAgentContext
            conversation_id: Parent conversation ID
        """
        tracker = ProgressTracker(len(contexts), conversation_id)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_with_retry(ctx: SubAgentContext) -> SubAgentContext:
            async with semaphore:
                await tracker.on_start(ctx.agent_id)
                ctx.started_at = datetime.now().isoformat()

                for attempt in range(1, self.max_retries + 1):
                    try:
                        # Apply timeout protection
                        ctx = await asyncio.wait_for(
                            run_func(ctx),
                            timeout=self.sub_agent_timeout,
                        )

                        if ctx.status in ("completed", "completed_with_limit"):
                            ctx.completed_at = datetime.now().isoformat()
                            await tracker.on_complete(ctx.agent_id, success=True)
                            return ctx

                        # Failed but retryable
                        if attempt < self.max_retries:
                            ctx.retry_count = attempt
                            await tracker.on_retry(ctx.agent_id, attempt)
                            # Exponential backoff
                            delay = self.retry_base_delay * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
                            # Reset context for retry but keep error history
                            ctx.messages = []
                            ctx.tool_steps = []
                            ctx.iterations = 0
                            continue

                    except asyncio.TimeoutError:
                        ctx.status = "timeout"
                        ctx.error = f"子代理执行超时（{self.sub_agent_timeout}秒限制）"
                        if attempt < self.max_retries:
                            await tracker.on_retry(ctx.agent_id, attempt)
                            ctx.retry_count = attempt
                            delay = self.retry_base_delay * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
                            ctx.messages = []
                            ctx.tool_steps = []
                            ctx.iterations = 0
                            continue

                    except Exception as exc:
                        ctx.status = "failed"
                        ctx.error = str(exc)
                        if attempt < self.max_retries:
                            await tracker.on_retry(ctx.agent_id, attempt)
                            ctx.retry_count = attempt
                            delay = self.retry_base_delay * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
                            ctx.messages = []
                            ctx.tool_steps = []
                            ctx.iterations = 0
                            continue

                # All retries exhausted
                ctx.completed_at = datetime.now().isoformat()
                if ctx.status not in ("completed", "completed_with_limit"):
                    ctx.status = "failed"
                await tracker.on_complete(ctx.agent_id, success=False)
                return ctx

        # Execute all sub-agents in parallel
        results = await asyncio.gather(
            *[_run_with_retry(ctx) for ctx in contexts],
            return_exceptions=False,
        )

        logger.info("Parallel execution complete: %s", tracker.summary())
        return list(results)


def build_reduce_summary(
    results: List[SubAgentContext],
    reduce_goal: str = "",
    llm_summary: str = "",
    run_id: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a structured reduce summary from sub-agent results."""
    lines = [
        "# Sub-Agent Reduce Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Total agents: {len(results)}",
        f"- Completed: {sum(1 for r in results if r.status in ('completed', 'completed_with_limit'))}",
        f"- Failed: {sum(1 for r in results if r.status == 'failed')}",
        f"- Timeout: {sum(1 for r in results if r.status == 'timeout')}",
    ]

    if config:
        lines.append(f"- Config: concurrency={config.get('max_concurrency')}, "
                      f"retries={config.get('max_retries')}, "
                      f"timeout={config.get('sub_agent_timeout')}s")

    if reduce_goal.strip():
        lines.append(f"- Reduce goal: {reduce_goal.strip()}")

    lines.extend(["", "## Agent Results", ""])

    # Group by status
    succeeded = [r for r in results if r.status in ("completed", "completed_with_limit")]
    failed = [r for r in results if r.status not in ("completed", "completed_with_limit")]

    if succeeded:
        lines.append("### Successful Results")
        lines.append("")
        for r in succeeded:
            lines.append(f"#### {r.item}")
            lines.append(f"- Status: {r.status}")
            lines.append(f"- Iterations: {r.iterations}")
            if r.retry_count > 0:
                lines.append(f"- Retries: {r.retry_count}")
            lines.append(f"- Answer: {r.final_answer[:500]}")
            lines.append("")

    if failed:
        lines.append("### Failed Results")
        lines.append("")
        for r in failed:
            lines.append(f"#### {r.item}")
            lines.append(f"- Status: {r.status}")
            lines.append(f"- Error: {r.error}")
            if r.retry_count > 0:
                lines.append(f"- Retries attempted: {r.retry_count}")
            lines.append("")

    if llm_summary:
        lines.extend([
            "## LLM Reduce Summary",
            "",
            llm_summary,
            "",
        ])

    return "\n".join(lines)
