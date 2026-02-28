"""Manus MVP - FastAPI 后端主入口（含计算机窗口 + 会话隔离）"""
import json
import sys
import os
import asyncio
import re
import logging
import io
import base64
import binascii
import mimetypes
import zipfile
from datetime import datetime
from pathlib import Path

# 配置日志，确保自定义 logger 的输出可见
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
    force=True,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from sse_starlette.sse import EventSourceResponse
from models.schemas import ChatRequest, SSEEventType
from agent.core import agent_engine
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_file_tree, read_file_content, get_workspace_root, delete_workspace
from sandbox.browser import browser_service

# 监控 API
try:
    from sandbox.monitor_api import register_monitor_api
    _HAS_MONITOR = True
except ImportError:
    _HAS_MONITOR = False

# Docker 沙箱补丁
try:
    from agent.tools_docker_patch import apply_docker_sandbox_patch
    apply_docker_sandbox_patch()
except Exception as _docker_patch_err:
    logging.getLogger("main").warning("无法应用 Docker 沙箱补丁，将使用进程级沙箱: %s", _docker_patch_err)

app = FastAPI(
    title="Manus MVP",
    description="AI Agent 系统 MVP - 含计算机窗口（会话隔离）",
    version="0.3.0"
)

API_TOKEN = os.environ.get("MANUS_API_TOKEN", "").strip()

# CORS 配置（默认仅允许本地前端）
_origins_raw = os.environ.get(
    "MANUS_ALLOWED_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
_allow_origins = [item.strip() for item in _origins_raw.split(",") if item.strip()]
if not _allow_origins:
    _allow_origins = ["http://localhost:3000", "http://127.0.0.1:3000"]
_allow_origin_regex = os.environ.get(
    "MANUS_ALLOWED_ORIGIN_REGEX",
    r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",
).strip() or None
_allow_credentials = "*" not in _allow_origins

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=None if "*" in _allow_origins else _allow_origin_regex,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _extract_bearer_token(header_value: str) -> str:
    if not isinstance(header_value, str):
        return ""
    text = header_value.strip()
    if not text.lower().startswith("bearer "):
        return ""
    return text[7:].strip()


def _is_authorized_http(request: Request) -> bool:
    if not API_TOKEN:
        return True
    auth_token = _extract_bearer_token(request.headers.get("authorization", ""))
    return bool(auth_token) and auth_token == API_TOKEN


def _is_authorized_ws(websocket: WebSocket) -> bool:
    if not API_TOKEN:
        return True

    header_token = _extract_bearer_token(websocket.headers.get("authorization", ""))
    query_token = websocket.query_params.get("token", "").strip()
    token = header_token or query_token
    return bool(token) and token == API_TOKEN


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
                logging.getLogger("main").info(
                    "对话 [%s] Docker 沙箱容器预热完成", conversation_id
                )
            except Exception as _e:
                logging.getLogger("main").warning("沙箱预热失败: %s", _e)

        asyncio.create_task(_preheat_sandbox())
        return True
    except Exception as _e:
        logging.getLogger("main").warning("无法调度 Docker 沙箱预热: %s", _e)
        return False


def _load_sub_agent_index(conversation_id: str):
    """
    读取子代理会话索引文件（若存在）。
    路径: <workspace>/multi_agent/sub_agent_index.json
    """
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


_SUB_AGENT_SESSION_ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")
_DATA_URL_RE = re.compile(r"^data:(?P<mime>[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,(?P<data>.+)$")
MAX_UPLOAD_IMAGE_BYTES = int(os.environ.get("MANUS_MAX_UPLOAD_IMAGE_BYTES", 6 * 1024 * 1024))
MAX_UPLOAD_IMAGE_COUNT = int(os.environ.get("MANUS_MAX_UPLOAD_IMAGE_COUNT", "4"))


def _sanitize_upload_filename(raw_name: str) -> str:
    name = Path(raw_name or "image").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe[:80] or "image"


def _pick_upload_extension(filename: str, mime_type: str) -> str:
    ext = Path(filename).suffix.lower()
    if ext in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg",
        ".heic", ".heif", ".tiff", ".tif",
    }:
        return ext
    guessed = (mimetypes.guess_extension(mime_type or "") or "").lower()
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def _decode_image_data_url(data_url: str):
    if not isinstance(data_url, str):
        return None
    text = data_url.strip()
    if not text:
        return None
    matched = _DATA_URL_RE.match(text)
    if not matched:
        return None
    mime_type = matched.group("mime").lower()
    if not mime_type.startswith("image/"):
        return None

    payload = matched.group("data")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > MAX_UPLOAD_IMAGE_BYTES:
        return None
    return mime_type, raw


def _persist_uploaded_images(conversation_id: str, images):
    if not images:
        return []
    try:
        workspace_root = Path(get_workspace_root(conversation_id)).resolve()
        upload_dir = (workspace_root / "uploads").resolve()
        upload_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []

    saved = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index, image in enumerate(images[:MAX_UPLOAD_IMAGE_COUNT], start=1):
        decoded = _decode_image_data_url(getattr(image, "data_url", ""))
        if not decoded:
            continue
        mime_type, raw = decoded

        filename_hint = _sanitize_upload_filename(getattr(image, "name", "") or "image")
        mime_hint = (getattr(image, "mime_type", "") or "").strip().lower()
        if mime_hint.startswith("image/"):
            mime_type = mime_hint
        stem = Path(filename_hint).stem or "image"
        ext = _pick_upload_extension(filename_hint, mime_type)
        filename = f"{timestamp}_{index:02d}_{stem}{ext}"
        target_path = (upload_dir / filename).resolve()

        if upload_dir != target_path and upload_dir not in target_path.parents:
            continue
        try:
            target_path.write_bytes(raw)
        except Exception:
            continue

        saved.append({
            "name": filename_hint,
            "mime_type": mime_type,
            "size_bytes": len(raw),
            "path": f"uploads/{filename}",
        })

    return saved


def _load_sub_agent_session(conversation_id: str, session_id: str):
    """
    读取指定子代理会话详情（若存在且归属于当前会话）。
    路径: <workspace>/multi_agent/sessions/<session_id>.json
    """
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


@app.middleware("http")
async def auth_middleware(request: Request, call_next):
    # 仅拦截业务 API，保留健康检查可匿名探活
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        if not _is_authorized_http(request):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "Manus MVP", "version": "0.3.0"}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """发送消息并获取 Agent 响应（SSE 流式）"""
    import logging as _logging
    _chat_logger = _logging.getLogger("main.chat")
    _chat_logger.info(
        "[ChatAPI] Received: message=%s, deep_research_enabled=%s, conversation_id=%s, images=%d",
        request.message[:60], request.deep_research_enabled, request.conversation_id, len(request.images or [])
    )
    if not request.message.strip() and not request.images:
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 先解析会话并加锁，避免同一 conversation 并发请求造成消息/计划状态错乱
    conversation = agent_engine.get_or_create_conversation(request.conversation_id)
    conversation_lock = agent_engine.get_conversation_lock(conversation.id)
    is_control_continue = bool(request.control_continue)
    uploaded_images = _persist_uploaded_images(conversation.id, request.images)
    if not request.message.strip() and not uploaded_images:
        raise HTTPException(status_code=400, detail="消息不能为空，或上传图片无效")

    # 对话创建后立即异步预热 Docker 沙箱容器，使监控仪表盘可见
    _schedule_docker_preheat(conversation.id)

    async def event_generator():
        registered_resume = False
        started_execution = False

        # control_continue 只保留一个排队/执行实例，避免刷新导致重复“继续”
        if is_control_continue:
            # 进程异常/崩溃后可能残留标记，锁空闲时自动清理
            if conversation.resume_pending and not conversation_lock.locked():
                conversation.resume_pending = False
                agent_engine._save_conversations()

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
            agent_engine._save_conversations()
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
                agent_engine._save_conversations()
                async for event in agent_engine.run_agent_loop(
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
                agent_engine._save_conversations()
        except asyncio.CancelledError:
            # 客户端中断连接（如刷新）后，标记为可恢复
            if started_execution:
                conversation.awaiting_resume = True
                agent_engine._save_conversations()
            raise
        finally:
            if registered_resume and conversation.resume_pending:
                conversation.resume_pending = False
                agent_engine._save_conversations()

    return EventSourceResponse(
        event_generator(),
        ping=10,
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/conversations")
async def list_conversations():
    """获取对话列表"""
    agent_engine.repair_conversation_titles()
    convs = []
    for conv in agent_engine.conversations.values():
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
            "manual_takeover_enabled": conv.manual_takeover_enabled,
            "manual_takeover_target": conv.manual_takeover_target,
            "limit_reached": conv.limit_reached,
            "continue_message": conv.continue_message,
            "awaiting_resume": conv.awaiting_resume,
            "resume_pending": conv.resume_pending,
            "plan_goal": conv.plan.goal if conv.plan else None,
            "plan_current_phase_id": running_phase,
        })
    convs.sort(key=lambda x: x["created_at"], reverse=True)
    return {"conversations": convs}


@app.post("/api/conversations")
async def create_conversation():
    """创建新对话并尝试预热沙箱容器。"""
    conv = agent_engine.get_or_create_conversation()
    _schedule_docker_preheat(conv.id)
    return {
        "id": conv.id,
        "title": conv.title,
        "message_count": len(conv.messages),
        "created_at": conv.created_at.isoformat(),
        "awaiting_resume": conv.awaiting_resume,
        "resume_pending": conv.resume_pending,
    }


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """获取对话详情"""
    agent_engine.repair_conversation_titles()
    if conversation_id not in agent_engine.conversations:
        raise HTTPException(status_code=404, detail="对话不存在")

    conv = agent_engine.conversations[conversation_id]
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
        "manual_takeover_enabled": conv.manual_takeover_enabled,
        "manual_takeover_target": conv.manual_takeover_target,
    }


@app.get("/api/conversations/{conversation_id}/sub-agents/{session_id}")
async def get_sub_agent_session(conversation_id: str, session_id: str):
    """获取单个子代理会话详情"""
    if conversation_id not in agent_engine.conversations:
        raise HTTPException(status_code=404, detail="对话不存在")

    payload = _load_sub_agent_session(conversation_id, session_id)
    if payload is None:
        raise HTTPException(status_code=404, detail="子代理会话不存在")

    return payload


@app.delete("/api/conversations/{conversation_id}")
async def delete_conversation(conversation_id: str):
    """删除会话及其运行时资源。"""
    deleted, reason = await agent_engine.delete_conversation(conversation_id)
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

    if not delete_workspace(conversation_id):
        cleanup_warnings.append("workspace_cleanup_failed")

    # 销毁对应的 Docker 沙箱容器
    try:
        from sandbox.docker_tools_adapter import _is_docker_available, sandbox_manager
        if _is_docker_available():
            await sandbox_manager.destroy_container(conversation_id)
    except Exception as e:
        cleanup_warnings.append(f"docker_container_cleanup_failed: {e}")

    return {"ok": True, "id": conversation_id, "warnings": cleanup_warnings}


# ============ 计算机窗口 API（支持会话隔离）============

@app.websocket("/ws/sandbox")
async def websocket_sandbox(websocket: WebSocket):
    """WebSocket 端点 - 实时推送沙箱事件到前端计算机窗口（支持按 conversation_id 过滤）"""
    if not _is_authorized_ws(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()

    # 订阅事件总线
    queue = event_bus.subscribe()

    # 当前订阅的 conversation_id（前端可通过消息切换）
    subscribed_conv_id = None

    def resolve_conversation_id(data: dict) -> str | None:
        return (data.get("conversation_id") or subscribed_conv_id) if isinstance(data, dict) else subscribed_conv_id

    def get_takeover_state(conversation_id: str | None) -> tuple[bool, str]:
        if not conversation_id:
            return False, "all"
        conv = agent_engine.conversations.get(conversation_id)
        if not conv:
            return False, "all"
        return bool(conv.manual_takeover_enabled), (conv.manual_takeover_target or "all")

    try:
        async def send_events():
            while True:
                event = await queue.get()
                try:
                    event_dict = event.to_dict()
                    # 如果前端订阅了特定 conversation_id，只推送该会话的事件
                    event_conv_id = event_dict.get("conversation_id")
                    if subscribed_conv_id is None:
                        # 未订阅具体会话时，仅推送全局事件，避免跨会话干扰
                        if event_conv_id is None:
                            await websocket.send_json(event_dict)
                    else:
                        if event_conv_id is None or event_conv_id == subscribed_conv_id:
                            await websocket.send_json(event_dict)
                except Exception:
                    break

        async def receive_messages():
            nonlocal subscribed_conv_id
            while True:
                try:
                    data = await websocket.receive_json()
                    msg_type = data.get("type", "")

                    if msg_type == "subscribe_conversation":
                        # 前端切换对话时，更新订阅的 conversation_id
                        subscribed_conv_id = data.get("conversation_id")
                        # 发送该会话的历史事件
                        if subscribed_conv_id:
                            history = event_bus.get_history(subscribed_conv_id)
                            for event_data in history[-30:]:
                                try:
                                    await websocket.send_json(event_data)
                                except Exception:
                                    break
                            takeover_enabled, takeover_target = get_takeover_state(subscribed_conv_id)
                            await websocket.send_json({
                                "type": "manual_takeover_changed",
                                "data": {
                                    "enabled": takeover_enabled,
                                    "target": takeover_target,
                                    "by": "system",
                                },
                                "window_id": "computer_control",
                                "conversation_id": subscribed_conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })

                    elif msg_type == "manual_takeover":
                        conv_id = resolve_conversation_id(data)
                        if not conv_id or conv_id not in agent_engine.conversations:
                            await websocket.send_json({
                                "type": "manual_takeover_changed",
                                "data": {
                                    "enabled": False,
                                    "target": "all",
                                    "by": "system",
                                    "error": "会话不存在，无法切换手动接管状态",
                                },
                                "window_id": "computer_control",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        enabled = bool(data.get("enabled", False))
                        target = str(data.get("target", "all")).strip().lower()
                        if target not in {"all", "terminal", "browser"}:
                            target = "all"

                        conv = agent_engine.conversations[conv_id]
                        conv.manual_takeover_enabled = enabled
                        conv.manual_takeover_target = target
                        agent_engine._save_conversations()

                        await event_bus.publish(SandboxEvent(
                            "manual_takeover_changed",
                            {
                                "enabled": enabled,
                                "target": target,
                                "by": "user",
                            },
                            window_id="computer_control",
                            conversation_id=conv_id,
                        ))

                    elif msg_type == "browser_navigate_manual":
                        conv_id = resolve_conversation_id(data)
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "navigate",
                                    "error": "当前未开启浏览器手动接管",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        raw_url = str(data.get("url", "") or "").strip()
                        if not raw_url:
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "navigate",
                                    "error": "URL 不能为空",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        if not re.match(r"^[a-zA-Z][a-zA-Z\d+\-.]*://", raw_url):
                            raw_url = f"https://{raw_url}"

                        result = await browser_service.navigate(
                            raw_url,
                            conversation_id=conv_id,
                        )
                        navigate_ok = bool(result.get("success", "error" not in result))
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {
                                "ok": navigate_ok,
                                "action": "navigate",
                                "error": result.get("error"),
                            },
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_click":
                        conv_id = resolve_conversation_id(data)
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "click",
                                    "error": "当前未开启浏览器手动接管",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        result = await browser_service.click_by_coordinates(
                            float(data.get("x", 0) or 0),
                            float(data.get("y", 0) or 0),
                            float(data.get("viewport_width", 0) or 0),
                            float(data.get("viewport_height", 0) or 0),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {
                                "ok": bool(result.get("success")),
                                "action": "click",
                                "error": result.get("error"),
                            },
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_type":
                        conv_id = resolve_conversation_id(data)
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "type",
                                    "error": "当前未开启浏览器手动接管",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        result = await browser_service.type_text(
                            str(data.get("text", "")),
                            bool(data.get("submit", False)),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {
                                "ok": bool(result.get("success")),
                                "action": "type",
                                "error": result.get("error"),
                            },
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_scroll":
                        conv_id = resolve_conversation_id(data)
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "scroll",
                                    "error": "当前未开启浏览器手动接管",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        result = await browser_service.scroll(
                            float(data.get("delta_y", 0) or 0),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {
                                "ok": bool(result.get("success")),
                                "action": "scroll",
                                "error": result.get("error"),
                            },
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_key":
                        conv_id = resolve_conversation_id(data)
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {
                                    "ok": False,
                                    "action": "key",
                                    "error": "当前未开启浏览器手动接管",
                                },
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        key = str(data.get("key", "Enter"))
                        if key not in {"Enter", "Tab", "Escape"}:
                            key = "Enter"

                        result = await browser_service.press_key(
                            key,
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {
                                "ok": bool(result.get("success")),
                                "action": "key",
                                "error": result.get("error"),
                            },
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "terminal_input":
                        from sandbox.terminal import terminal_manager
                        conv_id = resolve_conversation_id(data)
                        base_session_id = data.get("session_id", "default")
                        session_id = f"{conv_id}:{base_session_id}" if conv_id else base_session_id
                        input_data = data.get("data", "")
                        session = await terminal_manager.get_or_create(session_id, conversation_id=conv_id)
                        await session.write(input_data)

                    elif msg_type == "terminal_resize":
                        from sandbox.terminal import terminal_manager
                        conv_id = resolve_conversation_id(data)
                        base_session_id = data.get("session_id", "default")
                        session_id = f"{conv_id}:{base_session_id}" if conv_id else base_session_id
                        if session_id in terminal_manager.sessions:
                            session = terminal_manager.sessions[session_id]
                            cols = data.get("cols", 120)
                            rows = data.get("rows", 40)
                            session._set_size(cols, rows)

                except WebSocketDisconnect:
                    break
                except Exception:
                    break

        # 同时运行发送和接收
        send_task = asyncio.create_task(send_events())
        recv_task = asyncio.create_task(receive_messages())

        done, pending = await asyncio.wait(
            [send_task, recv_task],
            return_when=asyncio.FIRST_COMPLETED
        )

        for task in pending:
            task.cancel()

    except WebSocketDisconnect:
        pass
    finally:
        event_bus.unsubscribe(queue)


@app.get("/api/sandbox/files")
async def get_files(conversation_id: str = Query(None)):
    """获取沙箱文件树（按会话隔离）"""
    tree = await get_file_tree(conversation_id)
    root = get_workspace_root(conversation_id)
    return {"root": root, "tree": tree}


@app.get("/api/sandbox/files/download")
async def download_all_files(conversation_id: str = Query(None)):
    """下载当前会话工作目录中的全部文件（ZIP）。"""
    root = Path(get_workspace_root(conversation_id)).resolve()
    if not root.exists() or not root.is_dir():
        raise HTTPException(status_code=404, detail="工作目录不存在")

    def _build_zip_bytes() -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            file_count = 0
            for path in root.rglob("*"):
                if not path.is_file():
                    continue
                try:
                    resolved = path.resolve()
                except OSError:
                    continue
                if resolved != root and root not in resolved.parents:
                    continue

                rel_path = resolved.relative_to(root).as_posix()
                zf.write(str(resolved), arcname=rel_path)
                file_count += 1

            if file_count == 0:
                zf.writestr("README.txt", "Workspace is empty.")
        return buffer.getvalue()

    archive_bytes = await asyncio.to_thread(_build_zip_bytes)
    filename = f"{root.name}_files_{datetime.now().strftime('%Y%m%d_%H%M%S')}.zip"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    return StreamingResponse(io.BytesIO(archive_bytes), media_type="application/zip", headers=headers)


@app.get("/api/sandbox/files/content")
async def get_file_content(path: str, conversation_id: str = Query(None)):
    """获取文件内容（按会话隔离）"""
    result = await read_file_content(path, conversation_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@app.get("/api/sandbox/status")
async def get_sandbox_status():
    """获取沙箱状态"""
    from sandbox.terminal import terminal_manager

    return {
        "terminals": {
            sid: {"is_alive": s.is_alive}
            for sid, s in terminal_manager.sessions.items()
        },
        "browser": browser_service.get_status(),
        "workspace_base": "/tmp/manus_workspace",
    }


# ============ 注册监控 API ============
if _HAS_MONITOR:
    register_monitor_api(app)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
