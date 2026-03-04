"""
WebSocket 路由 — 从 main.py 中抽取的沙箱实时事件推送和手动接管逻辑。
"""

import asyncio
import re
from datetime import datetime

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from middleware.auth import is_authorized_ws
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.browser import browser_service


router = APIRouter(tags=["websocket"])


def _get_engine():
    """延迟导入 agent_engine，避免循环依赖。"""
    from agent.core import agent_engine
    return agent_engine


@router.websocket("/ws/sandbox")
async def websocket_sandbox(websocket: WebSocket):
    """WebSocket 端点 - 实时推送沙箱事件到前端计算机窗口（支持按 conversation_id 过滤）"""
    if not is_authorized_ws(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    await websocket.accept()
    engine = _get_engine()

    queue = event_bus.subscribe()
    subscribed_conv_id = None

    def resolve_conversation_id(data: dict) -> str | None:
        return (data.get("conversation_id") or subscribed_conv_id) if isinstance(data, dict) else subscribed_conv_id

    def get_takeover_state(conversation_id: str | None) -> tuple[bool, str]:
        if not conversation_id:
            return False, "all"
        conv = engine.conversations.get(conversation_id)
        if not conv:
            return False, "all"
        return bool(conv.manual_takeover_enabled), (conv.manual_takeover_target or "all")

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
                        if not conv_id or conv_id not in engine.conversations:
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

                        conv = engine.conversations[conv_id]
                        conv.manual_takeover_enabled = enabled
                        conv.manual_takeover_target = target
                        engine._save_conversations()

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
                                "data": {"ok": False, "action": "navigate", "error": "当前未开启浏览器手动接管"},
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

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
                        takeover_enabled, takeover_target = get_takeover_state(conv_id)
                        if not (takeover_enabled and takeover_target in {"all", "browser"}):
                            await websocket.send_json({
                                "type": "browser_interaction_result",
                                "data": {"ok": False, "action": "click", "error": "当前未开启浏览器手动接管"},
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
                            "data": {"ok": bool(result.get("success")), "action": "click", "error": result.get("error")},
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
                                "data": {"ok": False, "action": "type", "error": "当前未开启浏览器手动接管"},
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
                            "data": {"ok": bool(result.get("success")), "action": "type", "error": result.get("error")},
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
                                "data": {"ok": False, "action": "scroll", "error": "当前未开启浏览器手动接管"},
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
                            "data": {"ok": bool(result.get("success")), "action": "scroll", "error": result.get("error")},
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
                                "data": {"ok": False, "action": "key", "error": "当前未开启浏览器手动接管"},
                                "window_id": "browser",
                                "conversation_id": conv_id,
                                "timestamp": datetime.now().isoformat(),
                            })
                            continue

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
