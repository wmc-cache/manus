"""通过 docker exec 暴露容器内原始 TCP 端口。"""

import asyncio
import logging
import socket
from typing import Dict

logger = logging.getLogger("sandbox.raw_tcp_proxy")

_RAW_BRIDGE_SCRIPT = (
    "import socket,sys,threading,time\n"
    "deadline=time.time()+10\n"
    "while True:\n"
    " try:\n"
    "  s=socket.create_connection(('127.0.0.1',{port}),timeout=1)\n"
    "  s.settimeout(None)\n"
    "  break\n"
    " except OSError as e:\n"
    "  if time.time()>=deadline:\n"
    "   print(f'target not ready: {{e}}', file=sys.stderr, flush=True)\n"
    "   raise SystemExit(1)\n"
    "  time.sleep(0.2)\n"
    "def w():\n"
    " try:\n"
    "  while True:\n"
    "   d=sys.stdin.buffer.read1(65536)\n"
    "   if not d:break\n"
    "   s.sendall(d)\n"
    " except Exception:pass\n"
    " try:s.shutdown(socket.SHUT_WR)\n"
    " except Exception:pass\n"
    "threading.Thread(target=w,daemon=True).start()\n"
    "try:\n"
    " while True:\n"
    "  d=s.recv(65536)\n"
    "  if not d:break\n"
    "  sys.stdout.buffer.write(d)\n"
    "  sys.stdout.buffer.flush()\n"
    "except Exception:pass\n"
)


class DockerExecRawTCPProxy:
    """在宿主机监听随机端口，并桥接到容器内 TCP 服务。"""

    def __init__(self):
        self._tunnels: Dict[str, int] = {}
        self._servers: Dict[str, asyncio.AbstractServer] = {}

    async def create_tunnel(self, container_name: str, container_port: int, key: str) -> int:
        """创建到容器端口的本地隧道。"""
        if key in self._tunnels:
            return self._tunnels[key]

        script = _RAW_BRIDGE_SCRIPT.format(port=container_port)

        async def _client_to_proc(reader: asyncio.StreamReader, proc_stdin):
            try:
                while True:
                    data = await reader.read(65536)
                    if not data:
                        break
                    proc_stdin.write(data)
                    await proc_stdin.drain()
            except Exception:
                pass
            finally:
                try:
                    if proc_stdin and not proc_stdin.is_closing():
                        proc_stdin.close()
                except Exception:
                    pass

        async def _proc_to_client(proc_stdout: asyncio.StreamReader, writer: asyncio.StreamWriter):
            try:
                while True:
                    data = await proc_stdout.read(65536)
                    if not data:
                        break
                    writer.write(data)
                    await writer.drain()
            except Exception:
                pass
            finally:
                try:
                    writer.close()
                except Exception:
                    pass

        async def _log_stderr(proc_stderr: asyncio.StreamReader):
            try:
                while True:
                    line = await proc_stderr.readline()
                    if not line:
                        break
                    logger.debug("raw tcp bridge stderr: %s", line.decode(errors="replace").strip())
            except Exception:
                pass

        async def _handle(reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
            proc = None
            stderr_task = None
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "exec", "-i", container_name,
                    "python3", "-c", script,
                    stdin=asyncio.subprocess.PIPE,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stderr_task = asyncio.create_task(_log_stderr(proc.stderr))
                task_in = asyncio.create_task(_client_to_proc(reader, proc.stdin))
                task_out = asyncio.create_task(_proc_to_client(proc.stdout, writer))
                done, pending = await asyncio.wait(
                    [task_in, task_out],
                    return_when=asyncio.FIRST_COMPLETED,
                )
                for task in pending:
                    task.cancel()
                await asyncio.gather(*done, return_exceptions=True)
                await asyncio.gather(*pending, return_exceptions=True)
            except Exception as exc:
                logger.debug("raw tcp tunnel error: %s", exc)
                try:
                    writer.close()
                except Exception:
                    pass
            finally:
                if stderr_task:
                    stderr_task.cancel()
                if proc:
                    try:
                        proc.kill()
                    except Exception:
                        pass
                try:
                    writer.close()
                except Exception:
                    pass

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            local_port = sock.getsockname()[1]

        server = await asyncio.start_server(_handle, "127.0.0.1", local_port)
        self._tunnels[key] = local_port
        self._servers[key] = server
        logger.info(
            "Raw docker exec tunnel: 127.0.0.1:%d -> %s:%d",
            local_port,
            container_name,
            container_port,
        )
        return local_port

    def close_tunnel(self, key: str):
        """关闭本地隧道。"""
        server = self._servers.pop(key, None)
        if server:
            server.close()
        self._tunnels.pop(key, None)


docker_exec_raw_tcp_proxy = DockerExecRawTCPProxy()
