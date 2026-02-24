"""
Planner Module - Intelligent task planning with LLM-driven plan generation.

Key features:
1. LLM-driven plan generation instead of hardcoded 3-phase template
2. Dynamic plan revision based on execution progress
3. Plan complexity estimation for adaptive phase count
4. Structured plan output with clear phase dependencies
"""
import json
import logging
from typing import Any, Dict, List, Optional

from models.schemas import TaskPlan, PlanPhase, PlanPhaseStatus

logger = logging.getLogger(__name__)


# Plan generation prompt template
PLAN_GENERATION_PROMPT = """你是一个任务规划专家。请根据用户的请求，生成一个结构化的执行计划。

规则：
1. 将任务分解为 2-8 个阶段（简单任务 2-3 个，复杂任务 5-8 个）
2. 每个阶段应该是一个独立的工作单元
3. 最后一个阶段必须是"整理结果并交付"
4. 阶段标题要简洁明了，不超过 20 个字
5. 考虑阶段之间的依赖关系

请以 JSON 格式输出：
{
  "goal": "一句话描述任务目标",
  "phases": [
    {"id": 1, "title": "阶段标题"},
    {"id": 2, "title": "阶段标题"},
    ...
  ]
}

用户请求："""


PLAN_REVISION_PROMPT = """你是一个任务规划专家。当前计划执行遇到了新情况，需要修订计划。

当前计划：
{current_plan}

执行情况：
{execution_context}

请根据新情况修订计划。保留已完成的阶段，调整未完成的阶段。
输出格式同上。"""


class Planner:
    """Intelligent task planner with LLM-driven plan generation."""

    def __init__(self, llm_func=None):
        """
        Args:
            llm_func: async function for LLM completion (chat_completion).
                      If None, falls back to template-based planning.
        """
        self._llm_func = llm_func

    def estimate_complexity(self, user_message: str) -> str:
        """Estimate task complexity based on message analysis."""
        msg = user_message.strip().lower()
        length = len(msg)

        # Simple heuristics for complexity estimation
        complex_indicators = [
            "研究", "分析", "对比", "报告", "调研", "全面",
            "详细", "深入", "系统", "完整", "多个", "批量",
            "research", "analyze", "compare", "comprehensive",
            "deploy", "build", "create project", "开发", "部署",
        ]
        simple_indicators = [
            "什么是", "查一下", "搜索", "翻译", "计算",
            "what is", "search", "translate", "calculate",
        ]

        complex_count = sum(1 for ind in complex_indicators if ind in msg)
        simple_count = sum(1 for ind in simple_indicators if ind in msg)

        if complex_count >= 3 or length > 500:
            return "complex"
        elif simple_count >= 2 or length < 50:
            return "simple"
        return "medium"

    def create_template_plan(self, user_message: str) -> TaskPlan:
        """Create a plan using templates (fallback when LLM is unavailable)."""
        msg = user_message.strip().lower()
        complexity = self.estimate_complexity(user_message)
        goal = user_message.strip() or "完成用户请求"
        if len(goal) > 180:
            goal = goal[:180] + "..."

        search_like = any(token in msg for token in ["搜索", "检索", "查找", "调研", "research", "search", "latest"])
        coding_like = any(token in msg for token in ["代码", "修复", "bug", "实现", "开发", "api", "refactor", "test"])
        writing_like = any(token in msg for token in ["总结", "报告", "文档", "写作", "润色", "summary", "report", "write"])
        data_like = any(token in msg for token in ["数据", "分析", "统计", "图表", "csv", "sql", "pandas", "analysis"])

        if search_like:
            phases = [
                PlanPhase(id=1, title="明确检索范围与关键词", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="检索并筛选可信来源", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="提炼要点并整理结论", status=PlanPhaseStatus.PENDING),
            ]
        elif coding_like:
            phases = [
                PlanPhase(id=1, title="确认需求与约束", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="设计实现方案", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="修改代码并验证", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=4, title="整理变更并交付", status=PlanPhaseStatus.PENDING),
            ]
        elif data_like:
            phases = [
                PlanPhase(id=1, title="明确分析目标与口径", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="采集并清洗数据", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="完成分析与可视化", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=4, title="解释结果并给建议", status=PlanPhaseStatus.PENDING),
            ]
        elif writing_like:
            phases = [
                PlanPhase(id=1, title="梳理受众与输出目标", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="搭建结构并起草内容", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="润色校对并交付", status=PlanPhaseStatus.PENDING),
            ]
        elif complexity == "simple":
            phases = [
                PlanPhase(id=1, title="确认需求并执行", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="整理结果并回复", status=PlanPhaseStatus.PENDING),
            ]
        elif complexity == "complex":
            phases = [
                PlanPhase(id=1, title="分析需求并制定方案", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="收集信息与资料", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="执行核心任务", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=4, title="验证与完善", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=5, title="整理结果并交付", status=PlanPhaseStatus.PENDING),
            ]
        else:
            phases = [
                PlanPhase(id=1, title="理解需求并确认执行路径", status=PlanPhaseStatus.RUNNING),
                PlanPhase(id=2, title="调用工具逐步完成任务", status=PlanPhaseStatus.PENDING),
                PlanPhase(id=3, title="整理结果并给出最终回复", status=PlanPhaseStatus.PENDING),
            ]

        return TaskPlan(goal=goal, phases=phases, current_phase_id=1)

    async def create_plan_with_llm(self, user_message: str) -> Optional[TaskPlan]:
        """Create a plan using LLM for intelligent decomposition."""
        if not self._llm_func:
            return None

        try:
            messages = [
                {"role": "user", "content": PLAN_GENERATION_PROMPT + user_message},
            ]
            result = await self._llm_func(messages, use_tools=False)
            content = result.get("content", "")

            # Extract JSON from response
            plan_data = self._parse_plan_json(content)
            if not plan_data:
                return None

            goal = str(plan_data.get("goal", user_message[:180])).strip() or (user_message[:180] or "完成用户请求")
            raw_phases = plan_data.get("phases", [])
            if not isinstance(raw_phases, list) or len(raw_phases) < 2:
                return None

            phases: List[PlanPhase] = []
            for i, p in enumerate(raw_phases[:8]):
                if not isinstance(p, dict):
                    continue
                title = str(p.get("title", f"阶段 {i + 1}")).strip()[:40]
                if not title:
                    title = f"阶段 {i + 1}"
                status = PlanPhaseStatus.RUNNING if i == 0 else PlanPhaseStatus.PENDING
                phases.append(PlanPhase(
                    id=i + 1,
                    title=title,
                    status=status,
                ))
            if len(phases) < 2:
                return None

            return TaskPlan(goal=goal, phases=phases, current_phase_id=1)

        except Exception as exc:
            logger.warning("LLM plan generation failed: %s", exc)
            return None

    async def create_plan(self, user_message: str, use_llm: bool = True) -> TaskPlan:
        """Create a plan, trying LLM first then falling back to template."""
        if use_llm and self._llm_func:
            llm_plan = await self.create_plan_with_llm(user_message)
            if llm_plan:
                return llm_plan

        return self.create_template_plan(user_message)

    def advance_phase(self, plan: TaskPlan) -> bool:
        """Advance to the next phase in the plan."""
        if not plan or not plan.phases:
            return False

        current = next(
            (p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING),
            None,
        )
        if current:
            current.status = PlanPhaseStatus.COMPLETED

        # Find next pending phase
        next_phase = next(
            (p for p in plan.phases if p.status == PlanPhaseStatus.PENDING),
            None,
        )
        if next_phase:
            next_phase.status = PlanPhaseStatus.RUNNING
            plan.current_phase_id = next_phase.id
            return True

        return False

    def get_current_phase(self, plan: TaskPlan) -> Optional[PlanPhase]:
        """Get the currently running phase."""
        if not plan or not plan.phases:
            return None
        return next(
            (p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING),
            None,
        )

    def is_final_phase(self, plan: TaskPlan) -> bool:
        """Check if the current phase is the last one."""
        if not plan or not plan.phases:
            return False
        current = self.get_current_phase(plan)
        if not current:
            return False
        return current.id == plan.phases[-1].id

    @staticmethod
    def _parse_plan_json(text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from LLM response text."""
        # Try direct parse
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        # Try to find JSON block in markdown
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        # Try to find JSON object
        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None
