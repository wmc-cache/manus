"""
依赖注入容器 — 集中管理 Agent 子系统的创建和组装。

设计原则：
- 消除全局单例的直接实例化（如 `agent_engine = AgentEngine()`）
- 所有组件通过工厂函数创建，依赖关系显式声明
- 支持测试时替换任意组件（mock）
- 延迟初始化，避免模块导入时产生副作用
"""

import logging
from typing import Optional

from config.settings import settings
from agent.conversation_store import ConversationStore
from agent.plan_manager import PlanManager
from agent.tool_executor import ToolExecutor
from agent.message_builder import MessageBuilder
from agent.planner import Planner


logger = logging.getLogger(__name__)


class AgentDependencies:
    """
    Agent 依赖注入容器。

    用法：
        deps = AgentDependencies()
        engine = deps.create_engine()
    """

    def __init__(
        self,
        conversation_store: Optional[ConversationStore] = None,
        plan_manager: Optional[PlanManager] = None,
        tool_executor: Optional[ToolExecutor] = None,
        message_builder: Optional[MessageBuilder] = None,
    ):
        self._conversation_store = conversation_store
        self._plan_manager = plan_manager
        self._tool_executor = tool_executor
        self._message_builder = message_builder

    @property
    def conversation_store(self) -> ConversationStore:
        if self._conversation_store is None:
            self._conversation_store = ConversationStore()
        return self._conversation_store

    @property
    def plan_manager(self) -> PlanManager:
        if self._plan_manager is None:
            from llm.deepseek import chat_completion
            planner = Planner(llm_func=chat_completion)
            self._plan_manager = PlanManager(
                planner=planner,
                use_llm=settings.agent.plan_use_llm,
            )
        return self._plan_manager

    @property
    def tool_executor(self) -> ToolExecutor:
        if self._tool_executor is None:
            from agent.tools import execute_tool
            self._tool_executor = ToolExecutor(
                execute_tool_fn=execute_tool,
                parallel_safe_tools=settings.agent.parallel_safe_tools,
            )
        return self._tool_executor

    @property
    def message_builder(self) -> MessageBuilder:
        if self._message_builder is None:
            self._message_builder = MessageBuilder()
        return self._message_builder

    def create_engine(self):
        """创建完整的 AgentEngine 实例。"""
        from agent.core import AgentEngine
        return AgentEngine(
            store=self.conversation_store,
            plan_mgr=self.plan_manager,
            tool_exec=self.tool_executor,
            msg_builder=self.message_builder,
        )
