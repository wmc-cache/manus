"""数据模型定义"""
from pydantic import BaseModel, Field
from typing import Optional, List, Any, Dict
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


class ChatImage(BaseModel):
    name: str = ""
    mime_type: str = "application/octet-stream"
    data_url: Optional[str] = None
    size_bytes: Optional[int] = None
    path: Optional[str] = None


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None
    control_continue: bool = False
    deep_research_enabled: bool = False
    deep_research_max_concurrency: Optional[int] = None
    deep_research_max_items: Optional[int] = None
    deep_research_max_iterations: Optional[int] = None
    images: List[ChatImage] = Field(default_factory=list)


class ToolCall(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4())[:8])
    name: str
    arguments: dict = Field(default_factory=dict)
    result: Optional[Any] = None
    status: ToolCallStatus = ToolCallStatus.PENDING


class PlanPhase(BaseModel):
    id: int
    title: str
    status: PlanPhaseStatus = PlanPhaseStatus.PENDING
    capabilities: Dict[str, bool] = Field(default_factory=dict)  # Phase capability annotations


class TaskPlan(BaseModel):
    goal: str
    phases: List[PlanPhase] = Field(default_factory=list)
    current_phase_id: Optional[int] = None


class Message(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    role: MessageRole
    content: str = ""
    tool_calls: List[ToolCall] = Field(default_factory=list)
    images: List[ChatImage] = Field(default_factory=list)
    thinking: Optional[str] = None
    timestamp: datetime = Field(default_factory=datetime.now)


class Conversation(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str = "新对话"
    messages: List[Message] = Field(default_factory=list)
    plan: Optional[TaskPlan] = None
    plan_source: str = "template"
    manual_takeover_enabled: bool = False
    manual_takeover_target: str = "all"
    limit_reached: bool = False
    continue_message: Optional[str] = None
    awaiting_resume: bool = False
    resume_pending: bool = False
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
