"""
端口暴露服务 - 将沙箱容器内的 Web 服务代理到宿主机可访问的 URL

架构设计：
- Agent 调用 expose_port 工具后，在后端注册一个端口映射
- 后端通过反向代理将 /proxy/{port}/ 的请求转发到沙箱容器的对应端口
- 支持 Docker 模式（通过容器名访问）和进程模式（通过 localhost 访问）
- 前端通过 WebSocket 事件收到暴露的 URL，展示为可点击链接

网络路径：
  用户浏览器 -> 前端(3000) -> Vite Proxy -> 后端(8000) -> 沙箱容器(target_port)

macOS Docker Desktop 特殊处理：
  bridge 网络内的容器 IP/名称在 macOS 宿主机上不可达。
  解决方案：通过 docker exec -i 在容器内运行 Python TCP 桥接脚本，
  将宿主机本地端口的流量隧道到容器内部的目标端口。
"""

import asyncio
import logging
import os
import socket
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional

logger = logging.getLogger("sandbox.port_expose")


@dataclass
class ExposedPort:
    """表示一个已暴露的端口。"""
    port: int
    conversation_id: str
    label: str = ""
    created_at: float = field(default_factory=time.time)
    # 代理连接目标（tunnel 模式下为 127.0.0.1，进程/host 模式下为 localhost）
    internal_host: str = "localhost"
    # 实际连接端口（tunnel 模式下为本地隧道端口，否则与 port 相同）
    target_port: int = 0


# Python TCP 桥接脚本，在容器内运行，将 stdio 转发到容器内部指定端口
# 若目标端口未监听，直接返回 HTTP 502 响应，避免调用方无限等待
_BRIDGE_SCRIPT = (
    "import socket,sys,threading\n"
    "s=socket.socket()\n"
    "try:\n"
    " s.connect(('localhost',{port}))\n"
    "except Exception:\n"
    " sys.stdout.buffer.write(b'HTTP/1.1 502 Bad Gateway\\r\\nConnection: close\\r\\n\\r\\n')\n"
    " sys.stdout.buffer.flush()\n"
    " sys.exit(0)\n"
    "def r():\n"
    " try:\n"
    "  while True:\n"
    "   d=sys.stdin.buffer.read(4096)\n"
    "   if not d:break\n"
    "   s.sendall(d)\n"
    " except:pass\n"
    " try:s.shutdown(socket.SHUT_WR)\n"
    " except:pass\n"
    "threading.Thread(target=r,daemon=True).start()\n"
    "try:\n"
    " while True:\n"
    "  d=s.recv(4096)\n"
    "  if not d:break\n"
    "  sys.stdout.buffer.write(d)\n"
    "  sys.stdout.buffer.flush()\n"
    "except:pass\n"
)


class DockerExecTCPProxy:
    """
    通过 `docker exec -i` 在 macOS 上建立到容器的 TCP 隧道。

    原理：
    - 在本机监听一个随机端口
    - 每个连接 spawn 一个 docker exec 进程，运行 _BRIDGE_SCRIPT
    - _BRIDGE_SCRIPT 在容器内连接 localhost:{container_port} 并桥接 stdio
    - 宿主机 asyncio 负责把客户端 socket 和 docker exec stdin/stdout 互相转发
    """

    def __init__(self):
        self._tunnels: Dict[str, int] = {}          # key -> local_port
        self._servers: Dict[str, asyncio.AbstractServer] = {}

    async def create_tunnel(
        self,
        container_name: str,
        container_port: int,
        key: str,
    ) -> int:
        """启动隧道，返回宿主机本地监听端口。已存在则直接返回。"""
        if key in self._tunnels:
            return self._tunnels[key]

        script = _BRIDGE_SCRIPT.format(port=container_port)

        async def _pipe_request(reader: asyncio.StreamReader, proc_stdin: asyncio.StreamWriter):
            """
            从客户端读取 HTTP 请求并写入 proc.stdin。
            检测到请求头结束（空行 \\r\\n\\r\\n）后立即关闭 stdin，
            让 bridge script 的 r() 线程能在读到 EOF 后调用 s.shutdown(SHUT_WR)，
            从而让容器内 HTTP server 知道请求已完整接收。
            """
            buf = b""
            try:
                while True:
                    chunk = await reader.read(4096)
                    if not chunk:
                        break
                    buf += chunk
                    proc_stdin.write(chunk)
                    await proc_stdin.drain()
                    # HTTP 请求头以 \r\n\r\n 结束；对于 GET/HEAD 无请求体，这里即可关闭
                    if b"\r\n\r\n" in buf:
                        break
            except Exception:
                pass
            finally:
                try:
                    proc_stdin.close()
                except Exception:
                    pass

        async def _pipe_response(proc_stdout: asyncio.StreamReader, writer: asyncio.StreamWriter):
            """从 proc.stdout 读取响应并转发给客户端。"""
            wrote_any = False
            try:
                while True:
                    data = await proc_stdout.read(4096)
                    if not data:
                        break
                    wrote_any = True
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass

            if not wrote_any:
                # docker exec 失败（容器未运行等），发送兜底 502 响应
                body = b"Container not running or unavailable"
                try:
                    writer.write(
                        b"HTTP/1.1 502 Bad Gateway\r\n"
                        b"Content-Type: text/plain\r\n"
                        b"Content-Length: " + str(len(body)).encode() + b"\r\n"
                        b"Connection: close\r\n"
                        b"\r\n" + body
                    )
                    await writer.drain()
                except Exception:
                    pass

            try:
                writer.close()
            except Exception:
                pass

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            proc = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", "-i", container_name,
                    "python3", "-c", script,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.gather(
                    _pipe_request(reader, proc.stdin),
                    _pipe_response(proc.stdout, writer),
                    return_exceptions=True,
                )
            except Exception as exc:
                logger.debug("tunnel handle error: %s", exc)
            finally:
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    writer.close()
                except Exception:
                    pass

        # 找一个空闲本地端口
        with socket.socket() as _s:
            _s.bind(("127.0.0.1", 0))
            local_port = _s.getsockname()[1]

        server = await asyncio.start_server(_handle, "127.0.0.1", local_port)
        asyncio.get_event_loop().create_task(server.serve_forever())

        self._tunnels[key] = local_port
        self._servers[key] = server
        logger.info(
            "Docker exec TCP tunnel: 127.0.0.1:%d -> %s:%d",
            local_port, container_name, container_port,
        )
        return local_port

    def close_tunnel(self, key: str):
        server = self._servers.pop(key, None)
        if server:
            server.close()
        self._tunnels.pop(key, None)


# 全局 TCP 隧道管理器
docker_exec_proxy = DockerExecTCPProxy()


class PortExposeManager:
    """
    管理所有已暴露的端口映射。

    核心职责：
    1. 注册/注销端口暴露
    2. 根据 conversation_id 和 port 解析内部地址
    3. 提供反向代理所需的目标 URL
    """

    def __init__(self):
        # key: "{conversation_id}:{port}"
        self._exposed: Dict[str, ExposedPort] = {}

    def expose(
        self,
        port: int,
        conversation_id: Optional[str] = None,
        label: str = "",
        internal_host: str = "localhost",
        target_port: int = 0,
    ) -> ExposedPort:
        """注册一个端口暴露。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"

        entry = ExposedPort(
            port=port,
            conversation_id=cid,
            label=label or f"Port {port}",
            internal_host=internal_host,
            target_port=target_port,
        )
        self._exposed[key] = entry
        logger.info(
            "端口已暴露: %s:%d -> %s:%d (label=%s)",
            cid, port, internal_host, target_port or port, label,
        )
        return entry

    def unexpose(self, port: int, conversation_id: Optional[str] = None):
        """注销一个端口暴露。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"
        removed = self._exposed.pop(key, None)
        if removed:
            docker_exec_proxy.close_tunnel(key)
            logger.info("端口已关闭: %s:%d", cid, port)

    def get(self, port: int, conversation_id: Optional[str] = None) -> Optional[ExposedPort]:
        """获取指定端口的暴露信息。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"
        return self._exposed.get(key)

    def list_exposed(self, conversation_id: Optional[str] = None) -> List[ExposedPort]:
        """列出指定会话的所有已暴露端口。"""
        cid = conversation_id or "_default"
        return [
            ep for ep in self._exposed.values()
            if ep.conversation_id == cid
        ]

    def list_all(self) -> List[ExposedPort]:
        """列出所有已暴露端口。"""
        return list(self._exposed.values())

    def get_target_url(self, port: int, conversation_id: Optional[str] = None) -> Optional[str]:
        """获取反向代理的目标 URL。"""
        entry = self.get(port, conversation_id)
        if not entry:
            return None
        actual_port = entry.target_port if entry.target_port else entry.port
        return f"http://{entry.internal_host}:{actual_port}"

    def resolve_internal_host(self, conversation_id: Optional[str] = None) -> str:
        """
        解析沙箱容器的内部主机名（仅用于非隧道场景的初始判断）。

        - Docker host 模式：localhost（容器共享宿主机网络栈）
        - Docker bridge 模式：返回容器名（由调用方决定是否创建隧道）
        - 进程模式：localhost
        """
        docker_enabled = os.environ.get(
            "MANUS_DOCKER_SANDBOX", "true"
        ).strip().lower() in ("true", "1", "yes")

        if not docker_enabled:
            return "localhost"

        try:
            from sandbox.docker_sandbox import sandbox_manager, _container_name
            cid = conversation_id or "_default"

            if sandbox_manager._network_mode == "host":
                return "localhost"
            elif sandbox_manager._network_mode == "bridge":
                return _container_name(cid)
            else:
                return "localhost"
        except Exception:
            return "localhost"


# 全局实例
port_expose_manager = PortExposeManager()
