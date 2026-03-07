"""WebSocket 路由 — 沙箱实时事件推送与交互。"""

import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from middleware.auth import is_authorized_ws
from sandbox.event_bus import event_bus
from sandbox.browser import browser_service


router = APIRouter(tags=["websocket"])


@router.websocket("/ws/sandbox")
async def websocket_sandbox(websocket: WebSocket):
    """WebSocket 端点 - 实时推送沙箱事件到前端计算机窗口（支持按 conversation_id 过滤）"""
    if not is_authorized_ws(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()

    queue = event_bus.subscribe()
    subscribed_conv_id = None

    def resolve_conversation_id(data: dict) -> str | None:
        return (data.get("conversation_id") or subscribed_conv_id) if isinstance(data, dict) else subscribed_conv_id

    try:
        async def send_events():
            while True:
                event = await queue.get()
                try:
                    event_dict = event.to_dict()
                    event_conv_id = event_dict.get("conversation_id")
                    if subscribed_conv_id is None:
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
                        subscribed_conv_id = data.get("conversation_id")
                        if subscribed_conv_id:
                            history = event_bus.get_history(subscribed_conv_id)
                            for event_data in history[-30:]:
                                try:
                                    await websocket.send_json(event_data)
                                except Exception:
                                    break

                    elif msg_type == "browser_navigate":
                        conv_id = resolve_conversation_id(data)

                        raw_url = str(data.get("url", "") or "").strip()
                        if not raw_url:
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {"ok": False, "action": "navigate", "error": "URL 不能为空"},
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

                        if not re.match(r"^[a-zA-Z][a-zA-Z\d+\-.]*://", raw_url):
                            raw_url = f"https://{raw_url}"

                        result = await browser_service.navigate(raw_url, conversation_id=conv_id)
                        navigate_ok = bool(result.get("success", "error" not in result))
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {"ok": navigate_ok, "action": "navigate", "error": result.get("error")},
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_click":
                        conv_id = resolve_conversation_id(data)

                        result = await browser_service.click_by_coordinates(
                            float(data.get("x", 0) or 0),
                            float(data.get("y", 0) or 0),
                            float(data.get("viewport_width", 0) or 0),
                            float(data.get("viewport_height", 0) or 0),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {"ok": bool(result.get("success")), "action": "click", "error": result.get("error")},
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_type":
                        conv_id = resolve_conversation_id(data)

                        result = await browser_service.type_text(
                            str(data.get("text", "")),
                            bool(data.get("submit", False)),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {"ok": bool(result.get("success")), "action": "type", "error": result.get("error")},
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_scroll":
                        conv_id = resolve_conversation_id(data)

                        result = await browser_service.scroll(
                            float(data.get("delta_y", 0) or 0),
                            conversation_id=conv_id,
                        )
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {"ok": bool(result.get("success")), "action": "scroll", "error": result.get("error")},
                            "window_id": "browser",
                            "conversation_id": conv_id,
                            "timestamp": datetime.now().isoformat(),
                        })

                    elif msg_type == "browser_key":
                        conv_id = resolve_conversation_id(data)

                        key = str(data.get("key", "Enter"))
                        if key not in {"Enter", "Tab", "Escape"}:
                            key = "Enter"

                        result = await browser_service.press_key(key, conversation_id=conv_id)
                        await websocket.send_json({
                            "type": "browser_interaction_result",
                            "data": {"ok": bool(result.get("success")), "action": "key", "error": result.get("error")},
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
