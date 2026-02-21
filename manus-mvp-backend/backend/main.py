"""Manus MVP - FastAPI 后端主入口（含计算机窗口 + 会话隔离）"""
import json
import sys
import os
import asyncio
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from sse_starlette.sse import EventSourceResponse
from models.schemas import ChatRequest
from agent.core import agent_engine
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_file_tree, read_file_content, get_workspace_root
from sandbox.browser import browser_service

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
_allow_credentials = "*" not in _allow_origins

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
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
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    # 先解析会话并加锁，避免同一 conversation 并发请求造成消息/计划状态错乱
    conversation = agent_engine.get_or_create_conversation(request.conversation_id)
    conversation_lock = agent_engine.get_conversation_lock(conversation.id)

    async def event_generator():
        if conversation_lock.locked():
            yield {
                "event": "thinking",
                "data": json.dumps({
                    "iteration": 0,
                    "status": "queued",
                    "message": "当前会话有任务正在执行，已进入队列等待。",
                }, ensure_ascii=False),
            }

        async with conversation_lock:
            async for event in agent_engine.run_agent_loop(
                user_message=request.message,
                conversation_id=conversation.id
            ):
                yield {
                    "event": event["event"].value if hasattr(event["event"], 'value') else event["event"],
                    "data": event["data"]
                }

    return EventSourceResponse(event_generator())


@app.get("/api/conversations")
async def list_conversations():
    """获取对话列表"""
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
            "manual_takeover_enabled": conv.manual_takeover_enabled,
            "manual_takeover_target": conv.manual_takeover_target,
            "plan_goal": conv.plan.goal if conv.plan else None,
            "plan_current_phase_id": running_phase,
        })
    convs.sort(key=lambda x: x["created_at"], reverse=True)
    return {"conversations": convs}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str):
    """获取对话详情"""
    if conversation_id not in agent_engine.conversations:
        raise HTTPException(status_code=404, detail="对话不存在")

    conv = agent_engine.conversations[conversation_id]
    messages = []
    for msg in conv.messages:
        m = {
            "id": msg.id,
            "role": msg.role.value,
            "content": msg.content,
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
        "plan": conv.plan.model_dump(mode="json") if conv.plan else None,
        "created_at": conv.created_at.isoformat(),
        "manual_takeover_enabled": conv.manual_takeover_enabled,
        "manual_takeover_target": conv.manual_takeover_target,
    }


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


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
