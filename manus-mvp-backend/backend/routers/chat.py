"""
聊天相关路由 — 从 main.py 中抽取的 /api/chat 和 /api/conversations 端点。
"""

import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Query

from config.settings import settings
from llm.deepseek import llm_supports_vision
from models.schemas import ChatRequest, SSEEventType
from services.upload import persist_uploaded_images
from sandbox.filesystem import get_workspace_root

from sse_starlette.sse import EventSourceResponse


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["chat"])

# 子代理会话 ID 格式校验
_SUB_AGENT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


# ---------------------------------------------------------------------------
# 辅助函数（原 main.py 中的私有函数）
# ---------------------------------------------------------------------------

def _schedule_docker_preheat(conversation_id: str) -> bool:
    """异步预热指定会话的 Docker 沙箱容器。"""
    try:
        from sandbox.docker_tools_adapter import DOCKER_SANDBOX_ENABLED
        if not DOCKER_SANDBOX_ENABLED:
            return False

        from sandbox.docker_sandbox import sandbox_manager

        async def _preheat_sandbox():
            try:
                if not sandbox_manager._initialized:
                    await sandbox_manager.initialize()
                await sandbox_manager.get_or_create(conversation_id)
                logger.info("对话 [%s] Docker 沙箱容器预热完成", conversation_id)
            except Exception as _e:
                logger.warning("沙箱预热失败: %s", _e)

        asyncio.create_task(_preheat_sandbox())
        return True
    except Exception as _e:
        logger.warning("无法调度 Docker 沙箱预热: %s", _e)
        return False


def _load_sub_agent_index(conversation_id: str):
    """读取子代理会话索引文件（若存在）。"""
    try:
        workspace_root = Path(get_workspace_root(conversation_id))
        index_path = workspace_root / "multi_agent" / "sub_agent_index.json"
        if not index_path.exists():
            return None
        payload = json.loads(index_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None
        return payload
    except Exception:
        return None


def _load_sub_agent_session(conversation_id: str, session_id: str):
    """读取指定子代理会话详情（若存在且归属于当前会话）。"""
    sid = (session_id or "").strip()
    if not _SUB_AGENT_SESSION_ID_RE.fullmatch(sid):
        return None

    try:
        workspace_root = Path(get_workspace_root(conversation_id)).resolve()
        session_path = (workspace_root / "multi_agent" / "sessions" / f"{sid}.json").resolve()

        if workspace_root != session_path and workspace_root not in session_path.parents:
            return None
        if not session_path.exists():
            return None

        payload = json.loads(session_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            return None

        parent_id = payload.get("parent_conversation_id")
        if isinstance(parent_id, str) and parent_id and parent_id != conversation_id:
            return None

        return payload
    except Exception:
        return None


# ---------------------------------------------------------------------------
# 路由端点
# ---------------------------------------------------------------------------

def _get_engine():
    """延迟导入 agent_engine，避免循环依赖。"""
    from agent.core import agent_engine
    return agent_engine


@router.get("/health")
async def health_check():
    return {"status": "ok", "service": "Manus MVP", "version": settings.server.version}


@router.post("/chat")
async def chat(request: ChatRequest):
    """发送消息并获取 Agent 响应（SSE 流式）"""
    engine = _get_engine()
    logger.info(
        "[ChatAPI] Received: message=%s, deep_research_enabled=%s, conversation_id=%s, images=%d",
        request.message[:60], request.deep_research_enabled, request.conversation_id, len(request.images or [])
    )
    if not request.message.strip() and not request.images:
        raise HTTPException(status_code=400, detail="消息不能为空")

    conversation = engine.get_or_create_conversation(request.conversation_id)
    conversation_lock = engine.get_conversation_lock(conversation.id)
    is_control_continue = bool(request.control_continue)
    uploaded_images = persist_uploaded_images(conversation.id, request.images)
    if not request.message.strip() and not uploaded_images:
        raise HTTPException(status_code=400, detail="消息不能为空，或上传图片无效")

    if not (uploaded_images and not llm_supports_vision()):
        _schedule_docker_preheat(conversation.id)

    async def event_generator():
        registered_resume = False
        started_execution = False

        if is_control_continue:
            if conversation.resume_pending and not conversation_lock.locked():
                conversation.resume_pending = False
                engine._save_conversations()

            if conversation.resume_pending:
                yield {
                    "event": SSEEventType.THINKING.value,
                    "data": json.dumps({
                        "iteration": 0,
                        "status": "already_running",
                        "message": "当前会话已有自动续跑请求在执行或排队中。",
                    }, ensure_ascii=False),
                }
                yield {
                    "event": SSEEventType.DONE.value,
                    "data": json.dumps({
                        "conversation_id": conversation.id,
                        "iterations": 0,
                        "limit_reached": False,
                        "already_running": True,
                    }, ensure_ascii=False),
                }
                return

            conversation.resume_pending = True
            engine._save_conversations()
            registered_resume = True

        try:
            if conversation_lock.locked():
                queue_message = (
                    "当前会话有任务正在执行，已进入队列等待。"
                    if not is_control_continue
                    else "检测到任务仍在执行，已自动登记续跑请求，待当前任务结束后继续。"
                )
                yield {
                    "event": SSEEventType.THINKING.value,
                    "data": json.dumps({
                        "iteration": 0,
                        "status": "queued",
                        "message": queue_message,
                    }, ensure_ascii=False),
                }

            async with conversation_lock:
                started_execution = True
                conversation.awaiting_resume = False
                engine._save_conversations()
                async for event in engine.run_agent_loop(
                    user_message=request.message,
                    conversation_id=conversation.id,
                    record_user_message=not is_control_continue,
                    uploaded_images=uploaded_images,
                    deep_research_enabled=bool(request.deep_research_enabled),
                    deep_research_max_concurrency=request.deep_research_max_concurrency,
                    deep_research_max_items=request.deep_research_max_items,
                    deep_research_max_iterations=request.deep_research_max_iterations,
                ):
                    yield {
                        "event": event["event"].value if hasattr(event["event"], 'value') else event["event"],
                        "data": event["data"]
                    }
                conversation.awaiting_resume = False
                engine._save_conversations()
        except asyncio.CancelledError:
            if started_execution:
                conversation.awaiting_resume = True
                engine._save_conversations()
            raise
        finally:
            if registered_resume and conversation.resume_pending:
                conversation.resume_pending = False
                engine._save_conversations()

    return EventSourceResponse(
        event_generator(),
        ping=10,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@router.get("/conversations")
async def list_conversations():
    """获取对话列表"""
    engine = _get_engine()
    engine.repair_conversation_titles()
    convs = []
    for conv in engine.conversations.values():
        running_phase = None
        if conv.plan and conv.plan.phases:
            running_phase = next(
                (phase.id for phase in conv.plan.phases if phase.status.value == "running"),
                conv.plan.current_phase_id,
            )
        convs.append({
            "id": conv.id,
            "title": conv.title,
            "message_count": len(conv.messages),
            "created_at": conv.created_at.isoformat(),
            "plan_source": conv.plan_source,
            "limit_reached": conv.limit_reached,
            "continue_message": conv.continue_message,
            "awaiting_resume": conv.awaiting_resume,
            "resume_pending": conv.resume_pending,
            "plan_goal": conv.plan.goal if conv.plan else None,
            "plan_current_phase_id": running_phase,
        })
    convs.sort(key=lambda x: x["created_at"], reverse=True)
    return {"conversations": convs}


@router.post("/conversations")
async def create_conversation():
    """创建新对话并尝试预热沙箱容器。"""
    engine = _get_engine()
    conv = engine.get_or_create_conversation()
    _schedule_docker_preheat(conv.id)
    return {
        "id": conv.id,
        "title": conv.title,
        "message_count": len(conv.messages),
        "created_at": conv.created_at.isoformat(),
        "awaiting_resume": conv.awaiting_resume,
        "resume_pending": conv.resume_pending,
    }


@router.get("/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """获取对话详情"""
    engine = _get_engine()
    engine.repair_conversation_titles()
    if conversation_id not in engine.conversations:
        raise HTTPException(status_code=404, detail="对话不存在")

    conv = engine.conversations[conversation_id]
    messages = []
    for msg in conv.messages:
        m = {
            "id": msg.id,
            "role": msg.role.value,
            "content": msg.content,
            "images": [
                {
                    "name": image.name,
                    "mime_type": image.mime_type,
                    "size_bytes": image.size_bytes,
                    "path": image.path,
                }
                for image in msg.images
            ],
            "timestamp": msg.timestamp.isoformat(),
            "tool_calls": [
                {
                    "id": tc.id,
                    "name": tc.name,
                    "arguments": tc.arguments,
                    "result": tc.result,
                    "status": tc.status.value
                }
                for tc in msg.tool_calls
            ] if msg.tool_calls else []
        }
        messages.append(m)

    return {
        "id": conv.id,
        "title": conv.title,
        "messages": messages,
        "limit_reached": conv.limit_reached,
        "continue_message": conv.continue_message,
        "awaiting_resume": conv.awaiting_resume,
        "resume_pending": conv.resume_pending,
        "plan": conv.plan.model_dump(mode="json") if conv.plan else None,
        "plan_source": conv.plan_source,
        "sub_agent_index": _load_sub_agent_index(conv.id),
        "created_at": conv.created_at.isoformat(),
    }


@router.get("/conversations/{conversation_id}/sub-agents/{session_id}")
async def get_sub_agent_session(conversation_id: str, session_id: str):
    """获取单个子代理会话详情"""
    engine = _get_engine()
    if conversation_id not in engine.conversations:
        raise HTTPException(status_code=404, detail="对话不存在")

    payload = _load_sub_agent_session(conversation_id, session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="子代理会话不存在")

    return payload


@router.delete("/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除会话及其运行时资源。"""
    engine = _get_engine()
    from sandbox.browser import browser_service
    from sandbox.event_bus import event_bus

    deleted, reason = await engine.delete_conversation(conversation_id)
    if not deleted:
        if reason == "not_found":
            raise HTTPException(status_code=404, detail="对话不存在")
        if reason == "busy":
            raise HTTPException(status_code=409, detail="会话正在执行中，无法删除")
        raise HTTPException(status_code=500, detail="删除会话失败")

    cleanup_warnings = []

    try:
        from sandbox.terminal import terminal_manager
        await terminal_manager.close_conversation(conversation_id)
    except Exception:
        cleanup_warnings.append("terminal_cleanup_failed")

    try:
        await browser_service.close(conversation_id=conversation_id)
    except Exception:
        cleanup_warnings.append("browser_cleanup_failed")

    try:
        event_bus.clear_history(conversation_id)
    except Exception:
        cleanup_warnings.append("event_history_cleanup_failed")

    from sandbox.filesystem import delete_workspace
    if not delete_workspace(conversation_id):
        cleanup_warnings.append("workspace_cleanup_failed")

    try:
        from sandbox.docker_tools_adapter import _is_docker_available, sandbox_manager
        if _is_docker_available():
            await sandbox_manager.destroy_container(conversation_id)
    except Exception as e:
        cleanup_warnings.append(f"docker_container_cleanup_failed: {e}")

    return {"ok": True, "id": conversation_id, "warnings": cleanup_warnings}
