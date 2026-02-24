"""
Enhanced Parallel Processing Module — v2.

Key improvements over v1:
1. Independent context isolation per sub-agent (no shared state leakage)
2. Automatic retry with exponential backoff for failed sub-agents
3. Real-time progress tracking with SSE events
4. Structured error recovery and partial result aggregation
5. Resource-aware concurrency control
6. Sub-agent timeout protection
7. [NEW] Output schema validation for structured results
8. [NEW] Graceful degradation - partial results are always returned
9. [NEW] Better progress reporting with ETA estimation
10. [NEW] Sub-agent workspace isolation (each agent gets its own directory)
"""
import asyncio
import json
import logging
import os
import time
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
        output_schema: Optional[List[Dict[str, Any]]] = None,
    ):
        self.agent_id = agent_id
        self.item = item
        self.prompt = prompt
        self.workspace_dir = workspace_dir
        self.conversation_id = conversation_id
        self.max_iterations = max_iterations
        self.output_schema = output_schema  # Expected output fields

        # Independent message history (no shared state)
        self.messages: List[Dict[str, Any]] = []
        self.tool_steps: List[Dict[str, Any]] = []
        self.iterations = 0
        self.status = "pending"
        self.final_answer = ""
        self.structured_output: Dict[str, Any] = {}  # Validated structured output
        self.error = ""
        self.retry_count = 0
        self.started_at: Optional[str] = None
        self.completed_at: Optional[str] = None
        self.duration_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        return {
            "agent_id": self.agent_id,
            "item": self.item,
            "status": self.status,
            "iterations": self.iterations,
            "retry_count": self.retry_count,
            "final_answer": self.final_answer,
            "structured_output": self.structured_output,
            "error": self.error,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "duration_seconds": round(self.duration_seconds, 1),
        }

    def validate_output(self) -> bool:
        """Validate structured output against schema."""
        if not self.output_schema:
            return True
        for field in self.output_schema:
            name = field.get("name", "")
            required = field.get("required", False)
            if required and name not in self.structured_output:
                return False
        return True


class ProgressTracker:
    """Tracks and reports progress of parallel sub-agent execution with ETA."""

    def __init__(self, total: int, conversation_id: Optional[str] = None):
        self.total = total
        self.conversation_id = conversation_id
        self.completed = 0
        self.failed = 0
        self.running = 0
        self.start_time = time.time()
        self._completion_times: List[float] = []
        self._lock = asyncio.Lock()

    async def on_start(self, agent_id: str):
        async with self._lock:
            self.running += 1
            await self._emit_progress(f"子代理 {agent_id} 开始执行")

    async def on_complete(self, agent_id: str, success: bool, duration: float = 0.0):
        async with self._lock:
            self.running -= 1
            if success:
                self.completed += 1
            else:
                self.failed += 1
            self._completion_times.append(duration)
            status = "成功" if success else "失败"
            eta = self._estimate_eta()
            eta_str = f"，预计剩余 {eta:.0f}s" if eta > 0 else ""
            await self._emit_progress(
                f"子代理 {agent_id} 执行{status} ({self.completed + self.failed}/{self.total}{eta_str})"
            )

    async def on_retry(self, agent_id: str, attempt: int):
        async with self._lock:
            await self._emit_progress(f"子代理 {agent_id} 第 {attempt} 次重试")

    def _estimate_eta(self) -> float:
        """Estimate remaining time based on average completion time."""
        if not self._completion_times:
            return 0.0
        avg_time = sum(self._completion_times) / len(self._completion_times)
        remaining = self.total - self.completed - self.failed
        if remaining <= 0:
            return 0.0
        # Account for concurrency
        return avg_time * remaining / max(self.running, 1)

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
                    "elapsed_seconds": round(time.time() - self.start_time, 1),
                },
                window_id="terminal_default",
                conversation_id=self.conversation_id,
            ))
        except Exception as exc:
            logger.debug("Failed to emit progress: %s", exc)

    def summary(self) -> str:
        elapsed = time.time() - self.start_time
        return (
            f"总计: {self.total}, 成功: {self.completed}, "
            f"失败: {self.failed}, 成功率: "
            f"{self.completed / self.total * 100:.0f}%, "
            f"耗时: {elapsed:.1f}s"
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
        Always returns results (including partial) even if some agents fail.
        """
        tracker = ProgressTracker(len(contexts), conversation_id)
        semaphore = asyncio.Semaphore(self.max_concurrency)

        async def _run_with_retry(ctx: SubAgentContext) -> SubAgentContext:
            async with semaphore:
                await tracker.on_start(ctx.agent_id)
                ctx.started_at = datetime.now().isoformat()
                start_time = time.time()

                for attempt in range(1, self.max_retries + 1):
                    try:
                        ctx = await asyncio.wait_for(
                            run_func(ctx),
                            timeout=self.sub_agent_timeout,
                        )

                        if ctx.status in ("completed", "completed_with_limit"):
                            ctx.completed_at = datetime.now().isoformat()
                            ctx.duration_seconds = time.time() - start_time
                            await tracker.on_complete(
                                ctx.agent_id, success=True,
                                duration=ctx.duration_seconds,
                            )
                            return ctx

                        if attempt < self.max_retries:
                            ctx.retry_count = attempt
                            await tracker.on_retry(ctx.agent_id, attempt)
                            delay = self.retry_base_delay * (2 ** (attempt - 1))
                            await asyncio.sleep(delay)
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
                        logger.warning("Sub-agent %s failed: %s", ctx.agent_id, exc)
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
                ctx.duration_seconds = time.time() - start_time
                if ctx.status not in ("completed", "completed_with_limit"):
                    ctx.status = "failed"
                await tracker.on_complete(
                    ctx.agent_id, success=False,
                    duration=ctx.duration_seconds,
                )
                return ctx

        # Execute all sub-agents in parallel, catching individual exceptions
        tasks = [_run_with_retry(ctx) for ctx in contexts]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Handle any unexpected exceptions
        final_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                ctx = contexts[i]
                ctx.status = "failed"
                ctx.error = f"Unexpected error: {str(result)}"
                ctx.completed_at = datetime.now().isoformat()
                final_results.append(ctx)
            else:
                final_results.append(result)

        logger.info("Parallel execution complete: %s", tracker.summary())
        return final_results


def build_reduce_summary(
    results: List[SubAgentContext],
    reduce_goal: str = "",
    llm_summary: str = "",
    run_id: str = "",
    config: Optional[Dict[str, Any]] = None,
) -> str:
    """Build a structured reduce summary from sub-agent results."""
    succeeded = [r for r in results if r.status in ("completed", "completed_with_limit")]
    failed = [r for r in results if r.status not in ("completed", "completed_with_limit")]
    total_duration = sum(r.duration_seconds for r in results)

    lines = [
        "# Sub-Agent Reduce Summary",
        "",
        f"- Run ID: {run_id}",
        f"- Total agents: {len(results)}",
        f"- Completed: {len(succeeded)}",
        f"- Failed: {len(failed)}",
        f"- Total processing time: {total_duration:.1f}s",
    ]

    if config:
        lines.append(f"- Config: concurrency={config.get('max_concurrency')}, "
                      f"retries={config.get('max_retries')}, "
                      f"timeout={config.get('sub_agent_timeout')}s")

    if reduce_goal.strip():
        lines.append(f"- Reduce goal: {reduce_goal.strip()}")

    lines.extend(["", "## Agent Results", ""])

    if succeeded:
        lines.append("### Successful Results")
        lines.append("")
        for r in succeeded:
            lines.append(f"#### {r.item}")
            lines.append(f"- Status: {r.status} ({r.duration_seconds:.1f}s)")
            lines.append(f"- Iterations: {r.iterations}")
            if r.retry_count > 0:
                lines.append(f"- Retries: {r.retry_count}")
            # Include structured output if available
            if r.structured_output:
                lines.append("- Structured output:")
                for k, v in r.structured_output.items():
                    lines.append(f"  - {k}: {str(v)[:200]}")
            else:
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
            # Include partial results if any
            if r.final_answer:
                lines.append(f"- Partial answer: {r.final_answer[:200]}")
            lines.append("")

    if llm_summary:
        lines.extend([
            "## LLM Reduce Summary",
            "",
            llm_summary,
            "",
        ])

    return "\n".join(lines)
