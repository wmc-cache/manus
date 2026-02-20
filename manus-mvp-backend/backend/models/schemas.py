"""数据模型定义"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any
from enum import Enum
import uuid
from datetime import datetime


class MessageRole(str, Enum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class ToolCallStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class PlanPhaseStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    arguments: dict = {}
    result: Optional[Any] = None
    status: ToolCallStatus = ToolCallStatus.PENDING


class PlanPhase(BaseModel):
    id: int
    title: str
    status: PlanPhaseStatus = PlanPhaseStatus.PENDING


class TaskPlan(BaseModel):
    goal: str
    phases: List[PlanPhase] = []
    current_phase_id: Optional[int] = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str = ""
    tool_calls: List[ToolCall] = []
    thinking: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "新对话"
    messages: List[Message] = []
    plan: Optional[TaskPlan] = None
    created_at: datetime = Field(default_factory=datetime.now)


# SSE 事件类型
class SSEEventType(str, Enum):
    THINKING = "thinking"          # Agent 思考过程
    CONTENT = "content"            # 文本内容输出
    TOOL_CALL = "tool_call"        # 工具调用开始
    TOOL_RESULT = "tool_result"    # 工具调用结果
    PLAN_UPDATE = "plan_update"    # 任务计划更新
    DONE = "done"                  # 完成
    ERROR = "error"                # 错误
