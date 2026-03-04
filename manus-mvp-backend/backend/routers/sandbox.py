"""
沙箱相关路由 — 从 main.py 中抽取的文件管理、WebSocket 和沙箱状态端点。
"""

import asyncio
import io
import json
import re
import zipfile
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, Query

from fastapi.responses import StreamingResponse

from middleware.auth import is_authorized_ws
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import get_file_tree, read_file_content, get_workspace_root
from sandbox.browser import browser_service
from sandbox.port_expose import port_expose_manager


router = APIRouter(prefix="/api/sandbox", tags=["sandbox"])


# ---------------------------------------------------------------------------
# 文件管理
# ---------------------------------------------------------------------------

@router.get("/files")
async def get_files(conversation_id: str = Query(None)):
    """获取沙箱文件树（按会话隔离）"""
    tree = await get_file_tree(conversation_id)
    root = get_workspace_root(conversation_id)
    return {"root": root, "tree": tree}


@router.get("/files/download")
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


@router.get("/files/content")
async def get_file_content(path: str, conversation_id: str = Query(None)):
    """获取文件内容（按会话隔离）"""
    result = await read_file_content(path, conversation_id)
    if "error" in result:
        raise HTTPException(status_code=404, detail=result["error"])
    return result


@router.get("/status")
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


# ---------------------------------------------------------------------------
# 端口暴露
# ---------------------------------------------------------------------------

@router.get("/exposed-ports")
async def list_exposed_ports(conversation_id: str = Query(None)):
    """列出当前会话已暴露的端口"""
    ports = port_expose_manager.list_exposed(conversation_id)
    return {
        "ports": [
            {
                "port": ep.port,
                "label": ep.label,
                "url": f"/proxy/{ep.conversation_id}/{ep.port}/",
                "created_at": ep.created_at,
            }
            for ep in ports
        ]
    }


from fastapi import Request


@router.post("/expose-port")
async def api_expose_port(request: Request):
    """手动注册端口暴露（供测试或外部调用）"""
    body = await request.json()
    port_val = int(body.get("port", 0))
    conv_id = body.get("conversation_id", "_default")
    label = body.get("label", f"Port {port_val}")
    internal_host = body.get("internal_host", "localhost")

    if not port_val:
        raise HTTPException(status_code=400, detail="port 是必填参数")

    entry = port_expose_manager.expose(
        port=port_val,
        conversation_id=conv_id,
        label=label,
        internal_host=internal_host,
    )
    return {
        "port": entry.port,
        "label": entry.label,
        "url": f"/proxy/{entry.conversation_id}/{entry.port}/",
        "internal_host": entry.internal_host,
    }
