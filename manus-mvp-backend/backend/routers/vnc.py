"""
VNC WebSocket Relay — 将前端 noVNC 的 WebSocket 流量中转到沙箱容器的 VNC 服务 (端口 5900)。

通过 `docker exec -i` 在容器内运行 Python TCP 桥接脚本，
实现 WebSocket ↔ stdio ↔ TCP(5900) 的双向持久转发。
"""

import asyncio
import logging

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from starlette.websockets import WebSocketState

from middleware.auth import is_authorized_ws
from sandbox.docker_sandbox import sandbox_manager

logger = logging.getLogger(__name__)

router = APIRouter()

ENSURE_DESKTOP_COMMAND = """
if pgrep -f 'x11vnc .*5900' >/dev/null 2>&1; then
  python3 - <<'PY' >/dev/null 2>&1 && exit 0
import socket
s = socket.create_connection(("127.0.0.1", 5900), timeout=2)
s.close()
PY
fi

pkill -x x11vnc >/dev/null 2>&1 || true

if ! pgrep -f 'Xvfb :99' >/dev/null 2>&1; then
  rm -f /tmp/.X99-lock /tmp/.X11-unix/X99
  nohup Xvfb :99 -screen 0 1280x800x24 -ac +extension GLX +render -noreset >/tmp/xvfb.log 2>&1 < /dev/null &
  sleep 2
fi

export DISPLAY=:99
pgrep -x openbox >/dev/null 2>&1 || nohup openbox >/tmp/openbox.log 2>&1 < /dev/null &
sleep 1
pgrep -x x11vnc >/dev/null 2>&1 || nohup x11vnc -display :99 -forever -nopw -shared -rfbport 5900 -xkb >/tmp/x11vnc.log 2>&1 < /dev/null &

python3 - <<'PY'
import socket, time
deadline = time.time() + 15
while time.time() < deadline:
    try:
        s = socket.create_connection(("127.0.0.1", 5900), timeout=1)
        s.close()
        exit(0)
    except OSError:
        time.sleep(0.5)
exit(1)
PY
""".strip()

# ---------------------------------------------------------------------------
# 容器内 TCP 桥接脚本 — 连接 localhost:5900 并在 stdin/stdout 上双向转发
# ---------------------------------------------------------------------------
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
    """确保容器内桌面服务已启动，兼容旧容器仍在运行 sleep infinity 的情况。"""
    proc = await asyncio.create_subprocess_exec(
        "docker", "exec", container_name,
        "/bin/bash", "-lc", ENSURE_DESKTOP_COMMAND,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr = await proc.communicate()
    if proc.returncode != 0:
        error = stderr.decode(errors="replace").strip() or "unknown error"
        raise RuntimeError(f"desktop bootstrap failed: {error}")


@router.websocket("/ws/vnc/{conversation_id}")
async def vnc_relay(websocket: WebSocket, conversation_id: str):
    """VNC WebSocket relay: 前端 noVNC ↔ 沙箱 x11vnc"""

    # 鉴权
    if not is_authorized_ws(websocket):
        await websocket.close(code=1008, reason="Unauthorized")
        return

    try:
        if not sandbox_manager._initialized:
            await sandbox_manager.initialize()
        sandbox = await sandbox_manager.get_or_create(conversation_id)
        await _ensure_desktop_service(sandbox.container_name)
    except Exception as exc:
        logger.error("Failed to prepare VNC sandbox for %s: %s", conversation_id, exc)
        await websocket.close(code=1011, reason="Sandbox unavailable")
        return

    # noVNC 旧版本会请求 "binary" 子协议；新版本默认不带 subprotocol。
    # 如果客户端未声明支持的 subprotocol，服务端强行选择会触发浏览器立刻断开连接。
    offered_protocols = websocket.headers.get("sec-websocket-protocol", "")
    offered = {p.strip().lower() for p in offered_protocols.split(",") if p.strip()}
    if "binary" in offered:
        await websocket.accept(subprotocol="binary")
    else:
        await websocket.accept()

    container_name = sandbox.container_name

    # 启动 docker exec 桥接进程
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

    logger.info("VNC relay connected: conversation=%s container=%s", conversation_id, container_name)

    async def ws_to_proc():
        """WebSocket → docker exec stdin"""
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
        """docker exec stdout → WebSocket"""
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

    # 读取 stderr 用于调试
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
        # 清理
        try:
            proc.kill()
        except Exception:
            pass
        try:
            if websocket.client_state == WebSocketState.CONNECTED:
                await websocket.close()
        except Exception:
            pass
        logger.info("VNC relay disconnected: conversation=%s", conversation_id)
