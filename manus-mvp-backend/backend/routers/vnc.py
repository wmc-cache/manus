"""
VNC WebSocket Relay — 将前端 noVNC 的 WebSocket 流量中转到 VNC 服务 (端口 5900)。

支持两种模式：
1. Docker 模式（MANUS_USE_DOCKER=true）：通过 `docker exec -i` 在容器内运行 Python TCP 桥接脚本
2. 本地模式（默认）：直接连接本机 5900 端口，实现 WebSocket ↔ TCP(5900) 双向转发
"""

import asyncio
import logging
import os

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from middleware.auth import is_authorized_ws

logger = logging.getLogger(__name__)

router = APIRouter()

# 是否使用 Docker 模式
USE_DOCKER = os.environ.get("MANUS_USE_DOCKER", "false").lower() == "true"
VNC_HOST = os.environ.get("VNC_HOST", "localhost")
VNC_PORT = int(os.environ.get("VNC_PORT", "5900"))

# ---------------------------------------------------------------------------
# Docker 模式相关（保留向后兼容）
# ---------------------------------------------------------------------------
ENSURE_DESKTOP_COMMAND = """
set -e

wait_for_display() {
python3 - <<'PY'
import os
import subprocess
import time

env = dict(os.environ, DISPLAY=":99")
deadline = time.time() + 15

while time.time() < deadline:
    result = subprocess.run(
        ["xdpyinfo"],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        env=env,
    )
    if result.returncode == 0:
        raise SystemExit(0)
    time.sleep(0.5)

raise SystemExit("Xvfb display :99 not ready")
PY
}

wait_for_vnc_port() {
python3 - <<'PY'
import socket
import time

deadline = time.time() + 15
last_error = None

while time.time() < deadline:
    try:
        s = socket.create_connection(("127.0.0.1", 5900), timeout=1)
        s.close()
        raise SystemExit(0)
    except OSError as exc:
        last_error = exc
        time.sleep(0.5)

raise SystemExit(f"VNC port 5900 not ready: {last_error}")
PY
}

start_xvfb() {
  pkill -f 'Xvfb :99' >/dev/null 2>&1 || true
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  nohup Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 < /dev/null &
}

# 快速路径：x11vnc 已运行且端口可达
if pgrep -f 'x11vnc .*5900' >/dev/null 2>&1; then
  python3 - <<'PY' >/dev/null 2>&1 && exit 0
import socket
s = socket.create_connection(("127.0.0.1", 5900), timeout=2)
s.close()
PY
fi

pkill -x x11vnc >/dev/null 2>&1 || true

# 启动 Xvfb（若未运行）
if ! pgrep -f 'Xvfb :99' >/dev/null 2>&1; then
  start_xvfb
fi

if ! wait_for_display; then
  start_xvfb
  wait_for_display
fi

export DISPLAY=:99
if ! pgrep -x openbox >/dev/null 2>&1; then
  nohup openbox >/tmp/openbox.log 2>&1 < /dev/null &
fi
sleep 0.5

if ! pgrep -x x11vnc >/dev/null 2>&1; then
  nohup x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -xkb >/tmp/x11vnc.log 2>&1 < /dev/null &
fi

wait_for_vnc_port
""".strip()

VNC_BRIDGE_SCRIPT = (
    "import socket,sys,threading,time\n"
    "deadline=time.time()+10\n"
    "last_error=None\n"
    "while True:\n"
    " try:\n"
    "  s=socket.create_connection(('localhost',5900),timeout=1)\n"
    "  s.settimeout(None)\n"
    "  break\n"
    " except OSError as e:\n"
    "  last_error=e\n"
    "  if time.time()>=deadline:\n"
    "   print(f'VNC not ready: {e}', file=sys.stderr, flush=True)\n"
    "   raise SystemExit(1)\n"
    "  time.sleep(0.2)\n"
    "def w():\n"
    " try:\n"
    "  while True:\n"
    "   d=sys.stdin.buffer.read1(4096)\n"
    "   if not d:break\n"
    "   s.sendall(d)\n"
    " except:pass\n"
    " try:s.shutdown(socket.SHUT_WR)\n"
    " except:pass\n"
    "threading.Thread(target=w,daemon=True).start()\n"
    "try:\n"
    " while True:\n"
    "  d=s.recv(65536)\n"
    "  if not d:break\n"
    "  sys.stdout.buffer.write(d)\n"
    "  sys.stdout.buffer.flush()\n"
    "except:pass\n"
)


async def _ensure_desktop_service(container_name: str):
    """确保容器内桌面服务已启动（Docker 模式专用）。"""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container_name,
        "/bin/bash", "-c", ENSURE_DESKTOP_COMMAND,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        error = stderr.decode(errors="replace").strip() or "unknown error"
        raise RuntimeError(f"desktop bootstrap failed: {error}")


@router.websocket("/ws/vnc/{conversation_id}")
async def vnc_relay(websocket: WebSocket, conversation_id: str):
    """VNC WebSocket relay: 前端 noVNC ↔ VNC 服务"""

    # 鉴权
    if not is_authorized_ws(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    if USE_DOCKER:
        # Docker 模式：通过 sandbox_manager 获取容器
        try:
            from sandbox.docker_sandbox import sandbox_manager
            if not sandbox_manager._initialized:
                await sandbox_manager.initialize()
            sandbox = await sandbox_manager.get_or_create(conversation_id)
            await _ensure_desktop_service(sandbox.container_name)
        except Exception as exc:
            logger.error("Failed to prepare VNC sandbox for %s: %s", conversation_id, exc)
            await websocket.close(code=1011, reason="Sandbox unavailable")
            return
        await _relay_via_docker(websocket, sandbox.container_name, conversation_id)
    else:
        # 本地模式：直接连接本机 VNC 端口
        await _relay_local(websocket, conversation_id)


async def _relay_local(websocket: WebSocket, conversation_id: str):
    """本地模式：WebSocket ↔ 本机 TCP VNC 直连"""
    # 检查 VNC 端口是否可达
    try:
        import socket as _socket
        s = _socket.create_connection((VNC_HOST, VNC_PORT), timeout=3)
        s.close()
    except OSError as exc:
        logger.error("VNC port %s:%d not reachable: %s", VNC_HOST, VNC_PORT, exc)
        await websocket.close(code=1011, reason="VNC service unavailable")
        return

    # 接受 WebSocket 连接
    offered_protocols = websocket.headers.get("sec-websocket-protocol", "")
    offered = {p.strip().lower() for p in offered_protocols.split(",") if p.strip()}
    if "binary" in offered:
        await websocket.accept(subprotocol="binary")
    else:
        await websocket.accept()

    logger.info("VNC local relay connected: conversation=%s -> %s:%d", conversation_id, VNC_HOST, VNC_PORT)

    try:
        # 建立到 VNC 服务的 TCP 连接
        reader, writer = await asyncio.open_connection(VNC_HOST, VNC_PORT)
    except Exception as exc:
        logger.error("Failed to connect to VNC %s:%d: %s", VNC_HOST, VNC_PORT, exc)
        await websocket.close(code=1011, reason="VNC connect failed")
        return

    async def ws_to_vnc():
        """WebSocket → VNC TCP"""
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes") or (msg.get("text", "").encode() if msg.get("text") else None)
                if data:
                    writer.write(data)
                    await writer.drain()
        except (WebSocketDisconnect, Exception) as e:
            logger.debug("ws_to_vnc ended: %s", e)
        finally:
            try:
                writer.close()
            except Exception:
                pass

    async def vnc_to_ws():
        """VNC TCP → WebSocket"""
        try:
            while True:
                data = await reader.read(65536)
                if not data:
                    break
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_bytes(data)
                else:
                    break
        except (WebSocketDisconnect, Exception) as e:
            logger.debug("vnc_to_ws ended: %s", e)

    task_ws = asyncio.create_task(ws_to_vnc())
    task_vnc = asyncio.create_task(vnc_to_ws())

    try:
        done, pending = await asyncio.wait(
            [task_ws, task_vnc],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
        logger.info("VNC local relay disconnected: conversation=%s", conversation_id)


async def _relay_via_docker(websocket: WebSocket, container_name: str, conversation_id: str):
    """Docker 模式：WebSocket ↔ docker exec ↔ VNC TCP"""
    offered_protocols = websocket.headers.get("sec-websocket-protocol", "")
    offered = {p.strip().lower() for p in offered_protocols.split(",") if p.strip()}
    if "binary" in offered:
        await websocket.accept(subprotocol="binary")
    else:
        await websocket.accept()

    try:
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", "-i", container_name,
            "python3", "-c", VNC_BRIDGE_SCRIPT,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except Exception as exc:
        logger.error("Failed to start VNC bridge for %s: %s", conversation_id, exc)
        await websocket.close(code=1011, reason="Bridge start failed")
        return

    logger.info("VNC docker relay connected: conversation=%s container=%s", conversation_id, container_name)

    async def ws_to_proc():
        try:
            while True:
                msg = await websocket.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes") or (msg.get("text", "").encode() if msg.get("text") else None)
                if data and proc.stdin:
                    proc.stdin.write(data)
                    await proc.stdin.drain()
        except (WebSocketDisconnect, Exception) as e:
            logger.debug("ws_to_proc ended: %s", e)
        finally:
            if proc.stdin and not proc.stdin.is_closing():
                proc.stdin.close()

    async def proc_to_ws():
        try:
            while True:
                data = await proc.stdout.read(65536)
                if not data:
                    break
                if websocket.client_state == WebSocketState.CONNECTED:
                    await websocket.send_bytes(data)
                else:
                    break
        except (WebSocketDisconnect, Exception) as e:
            logger.debug("proc_to_ws ended: %s", e)

    async def log_stderr():
        try:
            while True:
                line = await proc.stderr.readline()
                if not line:
                    break
                logger.debug("VNC bridge stderr: %s", line.decode(errors="replace").strip())
        except Exception:
            pass

    task_ws = asyncio.create_task(ws_to_proc())
    task_proc = asyncio.create_task(proc_to_ws())
    task_err = asyncio.create_task(log_stderr())

    try:
        done, pending = await asyncio.wait(
            [task_ws, task_proc],
            return_when=asyncio.FIRST_COMPLETED,
        )
        for t in pending:
            t.cancel()
        task_err.cancel()
    finally:
        try:
            proc.kill()
        except Exception:
            pass
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
        logger.info("VNC docker relay disconnected: conversation=%s", conversation_id)
