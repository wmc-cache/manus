"""
计划状态机管理 — 从 agent/core.py 中抽取的计划生命周期管理逻辑。

职责：
- 计划的创建、初始化、恢复
- 计划阶段的状态转换（执行、推进、完成、失败）
- 计划序列化和 Markdown 渲染
- todo.md 文件持久化
"""

import json
import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from models.schemas import (
    Conversation,
    TaskPlan,
    PlanPhaseStatus,
)
from agent.planner import Planner
from sandbox.filesystem import get_workspace_root


logger = logging.getLogger(__name__)


class PlanManager:
    """管理 Agent 任务计划的完整生命周期。"""

    def __init__(self, planner: Planner, use_llm: bool = True):
        self._planner = planner
        self._use_llm = use_llm

    # ---- 计划创建与恢复 ----

    async def create_plan(self, user_message: str) -> tuple[TaskPlan, str]:
        """创建新计划，优先使用 LLM，失败时回退到模板。"""
        try:
            return await self._planner.create_plan_with_source(
                user_message=user_message,
                use_llm=self._use_llm,
            )
        except Exception as exc:
            logger.warning("Plan creation failed, fallback to template: %s", exc)
            return self._planner.create_template_plan(user_message), "template"

    async def ensure_plan_for_turn(self, conversation: Conversation, user_message: str) -> str:
        """确保当前轮次有可用的计划（初始化或恢复）。"""
        is_continue = user_message.strip().lower() in settings.agent.continue_messages

        if conversation.plan is None or not is_continue:
            plan, source = await self.create_plan(user_message)
            conversation.plan = plan
            conversation.plan_source = source
            return "initialized"

        plan = conversation.plan
        if not plan.phases:
            next_plan, source = await self.create_plan(user_message)
            conversation.plan = next_plan
            conversation.plan_source = source
            return "initialized"

        # 若无运行阶段，则继续当前未完成阶段
        running_phase = next((p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING), None)
        if running_phase is None:
            pending = next(
                (p for p in plan.phases if p.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}),
                None,
            )
            if pending:
                pending.status = PlanPhaseStatus.RUNNING
                plan.current_phase_id = pending.id
            else:
                last_phase = plan.phases[-1]
                last_phase.status = PlanPhaseStatus.RUNNING
                plan.current_phase_id = last_phase.id
        elif plan.current_phase_id != running_phase.id:
            plan.current_phase_id = running_phase.id
        return "resumed"

    # ---- 阶段状态转换 ----

    @staticmethod
    def transition_to_execution(plan: Optional[TaskPlan]) -> bool:
        """推进计划到执行阶段。"""
        if not plan or not plan.phases:
            return False

        changed = False
        phases = plan.phases
        running_idx = next(
            (idx for idx, phase in enumerate(phases) if phase.status == PlanPhaseStatus.RUNNING),
            None,
        )

        if running_idx is None:
            next_idx = next(
                (idx for idx, phase in enumerate(phases)
                 if phase.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}),
                None,
            )
            if next_idx is None:
                return False
            phases[next_idx].status = PlanPhaseStatus.RUNNING
            if plan.current_phase_id != phases[next_idx].id:
                plan.current_phase_id = phases[next_idx].id
            return True

        if plan.current_phase_id != phases[running_idx].id:
            plan.current_phase_id = phases[running_idx].id
            changed = True
        return changed

    @staticmethod
    def advance_phase(plan: Optional[TaskPlan]) -> bool:
        """显式推进计划到下一阶段。"""
        if not plan or not plan.phases:
            return False

        phases = plan.phases
        running_idx = next(
            (idx for idx, phase in enumerate(phases) if phase.status == PlanPhaseStatus.RUNNING),
            None,
        )
        if running_idx is None:
            return False

        phases[running_idx].status = PlanPhaseStatus.COMPLETED

        next_idx = next(
            (idx for idx, phase in enumerate(phases[running_idx + 1:], start=running_idx + 1)
             if phase.status in {PlanPhaseStatus.PENDING, PlanPhaseStatus.FAILED}),
            None,
        )
        if next_idx is not None:
            phases[next_idx].status = PlanPhaseStatus.RUNNING
            plan.current_phase_id = phases[next_idx].id
        else:
            plan.current_phase_id = phases[running_idx].id

        return True

    @staticmethod
    def mark_completed(plan: Optional[TaskPlan]) -> bool:
        """标记计划为全部完成。"""
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
    def transition_to_finalizing(plan: Optional[TaskPlan]) -> bool:
        """将计划推进到最终阶段。"""
        if not plan or not plan.phases:
            return False

        changed = False
        final_phase = plan.phases[-1]

        for phase in plan.phases[:-1]:
            if phase.status != PlanPhaseStatus.COMPLETED:
                phase.status = PlanPhaseStatus.COMPLETED
                changed = True

        if final_phase.status != PlanPhaseStatus.RUNNING:
            final_phase.status = PlanPhaseStatus.RUNNING
            changed = True

        if plan.current_phase_id != final_phase.id:
            plan.current_phase_id = final_phase.id
            changed = True

        return changed

    @staticmethod
    def mark_failed(plan: Optional[TaskPlan]) -> bool:
        """标记当前运行阶段为失败。"""
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

    # ---- 序列化与渲染 ----

    @staticmethod
    def serialize(plan: TaskPlan) -> Dict[str, Any]:
        """将计划序列化为字典。"""
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

    @staticmethod
    def to_markdown(plan: TaskPlan) -> str:
        """将计划渲染为 Markdown 格式。"""
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

    @staticmethod
    def persist_todo(conversation: Conversation) -> str:
        """将计划写入 todo.md 文件。"""
        if not conversation.plan:
            return ""
        try:
            root = get_workspace_root(conversation.id)
            todo_path = Path(root) / settings.agent.todo_filename
            todo_path.write_text(PlanManager.to_markdown(conversation.plan), encoding="utf-8")
            return str(todo_path)
        except Exception:
            return ""

    @staticmethod
    def build_update_payload(conversation: Conversation, reason: str) -> Dict[str, Any]:
        """构建计划更新事件的 payload。"""
        payload: Dict[str, Any] = {"reason": reason, "plan_source": conversation.plan_source}
        if conversation.plan:
            payload["plan"] = PlanManager.serialize(conversation.plan)
            todo_path = PlanManager.persist_todo(conversation)
            if todo_path:
                payload["todo_path"] = todo_path
        return payload
