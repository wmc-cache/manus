"""Manus MVP - FastAPI 后端主入口（含计算机窗口 + 会话隔离）"""
import json
import sys
import os
import asyncio

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI, HTTPException, WebSocket, WebSocketDisconnect, Query
from fastapi.middleware.cors import CORSMiddleware
from sse_starlette.sse import EventSourceResponse
from models.schemas import ChatRequest
from agent.core import agent_engine
from sandbox.event_bus import event_bus
from sandbox.filesystem import get_file_tree, read_file_content, get_workspace_root

app = FastAPI(
    title="Manus MVP",
    description="AI Agent 系统 MVP - 含计算机窗口（会话隔离）",
    version="0.3.0"
)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/api/health")
async def health_check():
    return {"status": "ok", "service": "Manus MVP", "version": "0.3.0"}


@app.post("/api/chat")
async def chat(request: ChatRequest):
    """发送消息并获取 Agent 响应（SSE 流式）"""
    if not request.message.strip():
        raise HTTPException(status_code=400, detail="消息不能为空")

    async def event_generator():
        async for event in agent_engine.run_agent_loop(
            user_message=request.message,
            conversation_id=request.conversation_id
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
        convs.append({
            "id": conv.id,
            "title": conv.title,
            "message_count": len(conv.messages),
            "created_at": conv.created_at.isoformat()
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
        "created_at": conv.created_at.isoformat()
    }


# ============ 计算机窗口 API（支持会话隔离）============

@app.websocket("/ws/sandbox")
async def websocket_sandbox(websocket: WebSocket):
    """WebSocket 端点 - 实时推送沙箱事件到前端计算机窗口（支持按 conversation_id 过滤）"""
    await websocket.accept()

    # 订阅事件总线
    queue = event_bus.subscribe()

    # 当前订阅的 conversation_id（前端可通过消息切换）
    subscribed_conv_id = None

    try:
        async def send_events():
            while True:
                event = await queue.get()
                try:
                    event_dict = event.to_dict()
                    # 如果前端订阅了特定 conversation_id，只推送该会话的事件
                    event_conv_id = event_dict.get("conversation_id")
                    if subscribed_conv_id is None or event_conv_id is None or event_conv_id == subscribed_conv_id:
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

                    elif msg_type == "terminal_input":
                        from sandbox.terminal import terminal_manager
                        session_id = data.get("session_id", "default")
                        input_data = data.get("data", "")
                        session = await terminal_manager.get_or_create(session_id)
                        await session.write(input_data)

                    elif msg_type == "terminal_resize":
                        from sandbox.terminal import terminal_manager
                        session_id = data.get("session_id", "default")
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
    from sandbox.browser import browser_service

    return {
        "terminals": {
            sid: {"is_alive": s.is_alive}
            for sid, s in terminal_manager.sessions.items()
        },
        "browser": {
            "is_ready": browser_service._is_ready,
            "current_url": browser_service._current_url,
        },
        "workspace_base": "/tmp/manus_workspace",
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
