"""
Planner Module — Enhanced Edition.

Key improvements:
1. LLM-driven plan generation with capability annotations per phase
2. Dynamic plan revision based on execution progress and new discoveries
3. Plan complexity estimation with multi-signal analysis
4. Phase capability metadata for dynamic tool gating
5. Plan update vs advance semantics (matching Manus 1.6 Max)
6. Better prompt engineering for plan generation
"""
import json
import logging
from typing import Any, Dict, List, Optional

from models.schemas import TaskPlan, PlanPhase, PlanPhaseStatus

logger = logging.getLogger(__name__)


# Plan generation prompt - enhanced with capability annotations
PLAN_GENERATION_PROMPT = """你是一个任务规划专家。请根据用户的请求，生成一个结构化的执行计划。

规则：
1. 将任务分解为 2-8 个阶段（简单任务 2-3 个，复杂任务 5-8 个）
2. 每个阶段应该是一个独立的工作单元
3. 最后一个阶段必须是"整理结果并交付"
4. 阶段标题要简洁明了，不超过 20 个字
5. 考虑阶段之间的依赖关系
6. 为每个阶段标注所需的能力（capabilities），可选值：
   - deep_research: 需要深入研究多个来源
   - data_analysis: 需要数据分析或可视化
   - web_development: 需要构建网站或应用
   - technical_writing: 需要技术或学术写作
   - creative_writing: 需要创意写作
   - parallel_processing: 需要并行处理多个子任务

请以 JSON 格式输出：
{
  "goal": "一句话描述任务目标",
  "phases": [
    {"id": 1, "title": "阶段标题", "capabilities": {"deep_research": true}},
    {"id": 2, "title": "阶段标题", "capabilities": {"data_analysis": true}},
    ...
  ]
}

用户请求："""


PLAN_REVISION_PROMPT = """你是一个任务规划专家。当前计划执行遇到了新情况，需要修订计划。

当前计划：
{current_plan}

已完成的阶段：
{completed_phases}

当前阶段：{current_phase}

新发现/变化：
{new_context}

请根据新情况修订计划。规则：
1. 保留已完成的阶段（状态标记为 completed）
2. 可以修改当前阶段和后续阶段
3. 可以增加或减少阶段
4. 最后一个阶段必须是"整理结果并交付"
5. 为每个阶段标注所需的能力

输出格式同上。"""


class Planner:
    """Intelligent task planner with LLM-driven plan generation and revision."""

    def __init__(self, llm_func=None):
        """
        Args:
            llm_func: async function for LLM completion (chat_completion).
                      If None, falls back to template-based planning.
        """
        self._llm_func = llm_func

    def estimate_complexity(self, user_message: str) -> str:
        """Estimate task complexity based on multi-signal analysis."""
        msg = user_message.strip().lower()
        length = len(msg)

        complex_indicators = [
            "研究", "分析", "对比", "报告", "调研", "全面",
            "详细", "深入", "系统", "完整", "多个", "批量",
            "research", "analyze", "compare", "comprehensive",
            "deploy", "build", "create project", "开发", "部署",
            "优化", "重构", "架构", "设计", "方案",
        ]
        simple_indicators = [
            "什么是", "查一下", "搜索", "翻译", "计算",
            "what is", "search", "translate", "calculate",
            "帮我", "告诉我", "解释",
        ]

        complex_count = sum(1 for ind in complex_indicators if ind in msg)
        simple_count = sum(1 for ind in simple_indicators if ind in msg)

        # Multi-signal scoring
        score = complex_count * 2 - simple_count
        if length > 500:
            score += 3
        elif length > 200:
            score += 1
        elif length < 30:
            score -= 2

        if score >= 4:
            return "complex"
        elif score <= -1 or (simple_count >= 2 and complex_count == 0):
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
        coding_like = any(token in msg for token in ["代码", "修复", "bug", "实现", "开发", "api", "refactor", "test", "优化"])
        writing_like = any(token in msg for token in ["总结", "报告", "文档", "写作", "润色", "summary", "report", "write"])
        data_like = any(token in msg for token in ["数据", "分析", "统计", "图表", "csv", "sql", "pandas", "analysis"])
        web_like = any(token in msg for token in ["网站", "网页", "前端", "后端", "website", "web", "app", "deploy"])

        if web_like:
            phases = [
                PlanPhase(id=1, title="分析需求与技术选型", status=PlanPhaseStatus.RUNNING,
                          capabilities={"technical_writing": True}),
                PlanPhase(id=2, title="搭建项目框架", status=PlanPhaseStatus.PENDING,
                          capabilities={"web_development": True}),
                PlanPhase(id=3, title="实现核心功能", status=PlanPhaseStatus.PENDING,
                          capabilities={"web_development": True}),
                PlanPhase(id=4, title="测试与优化", status=PlanPhaseStatus.PENDING,
                          capabilities={"web_development": True}),
                PlanPhase(id=5, title="整理结果并交付", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        elif search_like:
            phases = [
                PlanPhase(id=1, title="明确检索范围与关键词", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="检索并筛选可信来源", status=PlanPhaseStatus.PENDING,
                          capabilities={"deep_research": True}),
                PlanPhase(id=3, title="提炼要点并整理结论", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        elif coding_like:
            phases = [
                PlanPhase(id=1, title="确认需求与约束", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="设计实现方案", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
                PlanPhase(id=3, title="修改代码并验证", status=PlanPhaseStatus.PENDING,
                          capabilities={"web_development": True}),
                PlanPhase(id=4, title="整理变更并交付", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        elif data_like:
            phases = [
                PlanPhase(id=1, title="明确分析目标与口径", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="采集并清洗数据", status=PlanPhaseStatus.PENDING,
                          capabilities={"data_analysis": True}),
                PlanPhase(id=3, title="完成分析与可视化", status=PlanPhaseStatus.PENDING,
                          capabilities={"data_analysis": True}),
                PlanPhase(id=4, title="解释结果并给建议", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        elif writing_like:
            phases = [
                PlanPhase(id=1, title="梳理受众与输出目标", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="搭建结构并起草内容", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
                PlanPhase(id=3, title="润色校对并交付", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        elif complexity == "simple":
            phases = [
                PlanPhase(id=1, title="确认需求并执行", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="整理结果并回复", status=PlanPhaseStatus.PENDING,
                          capabilities={}),
            ]
        elif complexity == "complex":
            phases = [
                PlanPhase(id=1, title="分析需求并制定方案", status=PlanPhaseStatus.RUNNING,
                          capabilities={"deep_research": True}),
                PlanPhase(id=2, title="收集信息与资料", status=PlanPhaseStatus.PENDING,
                          capabilities={"deep_research": True}),
                PlanPhase(id=3, title="执行核心任务", status=PlanPhaseStatus.PENDING,
                          capabilities={"web_development": True}),
                PlanPhase(id=4, title="验证与完善", status=PlanPhaseStatus.PENDING,
                          capabilities={}),
                PlanPhase(id=5, title="整理结果并交付", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
            ]
        else:
            phases = [
                PlanPhase(id=1, title="理解需求并确认执行路径", status=PlanPhaseStatus.RUNNING,
                          capabilities={}),
                PlanPhase(id=2, title="调用工具逐步完成任务", status=PlanPhaseStatus.PENDING,
                          capabilities={}),
                PlanPhase(id=3, title="整理结果并给出最终回复", status=PlanPhaseStatus.PENDING,
                          capabilities={"technical_writing": True}),
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

            plan_data = self._parse_plan_json(content)
            if not plan_data:
                return None

            goal = str(plan_data.get("goal", user_message[:180])).strip() or (user_message[:180] or "完成用户请求")
            raw_phases = plan_data.get("phases", [])
            if not isinstance(raw_phases, list) or len(raw_phases) < 2:
                return None

            phases: List[PlanPhase] = []
            for i, p in enumerate(raw_phases[:10]):  # Allow up to 10 phases
                if not isinstance(p, dict):
                    continue
                title = str(p.get("title", f"阶段 {i + 1}")).strip()[:40]
                if not title:
                    title = f"阶段 {i + 1}"
                status = PlanPhaseStatus.RUNNING if i == 0 else PlanPhaseStatus.PENDING

                # Parse capabilities
                capabilities = {}
                raw_caps = p.get("capabilities", {})
                if isinstance(raw_caps, dict):
                    for cap_name, enabled in raw_caps.items():
                        if isinstance(cap_name, str) and enabled:
                            capabilities[cap_name] = True

                phases.append(PlanPhase(
                    id=i + 1,
                    title=title,
                    status=status,
                    capabilities=capabilities,
                ))
            if len(phases) < 2:
                return None

            return TaskPlan(goal=goal, phases=phases, current_phase_id=1)

        except Exception as exc:
            logger.warning("LLM plan generation failed: %s", exc)
            return None

    async def revise_plan_with_llm(
        self,
        plan: TaskPlan,
        new_context: str,
    ) -> Optional[TaskPlan]:
        """
        Revise an existing plan based on new discoveries.
        This is the 'update' action in Manus 1.6 Max's plan tool.
        """
        if not self._llm_func or not plan:
            return None

        try:
            completed = [
                f"  - [{p.id}] {p.title} ✓"
                for p in plan.phases if p.status == PlanPhaseStatus.COMPLETED
            ]
            current = next(
                (p for p in plan.phases if p.status == PlanPhaseStatus.RUNNING),
                None,
            )

            prompt = PLAN_REVISION_PROMPT.format(
                current_plan=json.dumps(
                    {"goal": plan.goal, "phases": [{"id": p.id, "title": p.title} for p in plan.phases]},
                    ensure_ascii=False,
                ),
                completed_phases="\n".join(completed) if completed else "  (无)",
                current_phase=f"[{current.id}] {current.title}" if current else "(无)",
                new_context=new_context,
            )

            messages = [{"role": "user", "content": prompt}]
            result = await self._llm_func(messages, use_tools=False)
            content = result.get("content", "")

            plan_data = self._parse_plan_json(content)
            if not plan_data:
                return None

            goal = str(plan_data.get("goal", plan.goal)).strip() or plan.goal
            raw_phases = plan_data.get("phases", [])
            if not isinstance(raw_phases, list) or len(raw_phases) < 2:
                return None

            # Build revised plan, preserving completed phase statuses
            completed_ids = {p.id for p in plan.phases if p.status == PlanPhaseStatus.COMPLETED}
            phases: List[PlanPhase] = []
            first_pending = True

            for i, p in enumerate(raw_phases[:10]):
                if not isinstance(p, dict):
                    continue
                title = str(p.get("title", f"阶段 {i + 1}")).strip()[:40]
                phase_id = i + 1

                capabilities = {}
                raw_caps = p.get("capabilities", {})
                if isinstance(raw_caps, dict):
                    for cap_name, enabled in raw_caps.items():
                        if isinstance(cap_name, str) and enabled:
                            capabilities[cap_name] = True

                if phase_id in completed_ids:
                    status = PlanPhaseStatus.COMPLETED
                elif first_pending:
                    status = PlanPhaseStatus.RUNNING
                    first_pending = False
                else:
                    status = PlanPhaseStatus.PENDING

                phases.append(PlanPhase(
                    id=phase_id,
                    title=title,
                    status=status,
                    capabilities=capabilities,
                ))

            if len(phases) < 2:
                return None

            current_id = next(
                (p.id for p in phases if p.status == PlanPhaseStatus.RUNNING),
                phases[0].id,
            )

            return TaskPlan(goal=goal, phases=phases, current_phase_id=current_id)

        except Exception as exc:
            logger.warning("LLM plan revision failed: %s", exc)
            return None

    async def create_plan(self, user_message: str, use_llm: bool = True) -> TaskPlan:
        """Create a plan, trying LLM first then falling back to template."""
        plan, _ = await self.create_plan_with_source(user_message, use_llm=use_llm)
        return plan

    async def create_plan_with_source(
        self,
        user_message: str,
        use_llm: bool = True,
    ) -> tuple[TaskPlan, str]:
        """Create plan and return source (`llm` or `template`)."""
        if use_llm and self._llm_func:
            llm_plan = await self.create_plan_with_llm(user_message)
            if llm_plan:
                return llm_plan, "llm"

        return self.create_template_plan(user_message), "template"

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

    def get_progress_summary(self, plan: TaskPlan) -> str:
        """Get a human-readable progress summary."""
        if not plan or not plan.phases:
            return "无计划"

        completed = sum(1 for p in plan.phases if p.status == PlanPhaseStatus.COMPLETED)
        total = len(plan.phases)
        current = self.get_current_phase(plan)
        current_title = current.title if current else "未知"

        return f"进度: {completed}/{total} | 当前: {current_title}"

    @staticmethod
    def _parse_plan_json(text: str) -> Optional[Dict[str, Any]]:
        """Extract JSON from LLM response text."""
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            pass

        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", text, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass

        brace_start = text.find("{")
        brace_end = text.rfind("}")
        if brace_start >= 0 and brace_end > brace_start:
            try:
                return json.loads(text[brace_start:brace_end + 1])
            except json.JSONDecodeError:
                pass

        return None
