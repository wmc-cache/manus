"""终端服务 - 管理 shell 会话和命令执行"""
import asyncio
import os
import pty
import select
import signal
import struct
import fcntl
import termios
from typing import Optional, Dict
from sandbox.event_bus import event_bus, SandboxEvent


class TerminalSession:
    """单个终端会话"""

    def __init__(self, session_id: str, cwd: str = "/tmp/manus_workspace"):
        self.session_id = session_id
        self.cwd = cwd
        self.master_fd: Optional[int] = None
        self.pid: Optional[int] = None
        self.is_alive = False
        self._output_buffer = ""
        self._read_task: Optional[asyncio.Task] = None

    async def start(self):
        """启动终端会话"""
        os.makedirs(self.cwd, exist_ok=True)

        # 创建伪终端
        pid, master_fd = pty.fork()

        if pid == 0:
            # 子进程 - 执行 shell
            os.chdir(self.cwd)
            os.environ["TERM"] = "xterm-256color"
            os.environ["PS1"] = "\\[\\033[01;32m\\]manus\\[\\033[00m\\]:\\[\\033[01;34m\\]\\w\\[\\033[00m\\]$ "
            os.execvp("/bin/bash", ["/bin/bash", "--norc", "-i"])
        else:
            # 父进程
            self.pid = pid
            self.master_fd = master_fd
            self.is_alive = True

            # 设置非阻塞
            flags = fcntl.fcntl(master_fd, fcntl.F_GETFL)
            fcntl.fcntl(master_fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)

            # 设置终端大小
            self._set_size(120, 40)

            # 启动输出读取任务
            self._read_task = asyncio.create_task(self._read_output())

            await event_bus.publish(SandboxEvent(
                "terminal_started",
                {"session_id": self.session_id},
                window_id=f"terminal_{self.session_id}"
            ))

    def _set_size(self, cols: int, rows: int):
        """设置终端大小"""
        if self.master_fd is not None:
            winsize = struct.pack("HHHH", rows, cols, 0, 0)
            fcntl.ioctl(self.master_fd, termios.TIOCSWINSZ, winsize)

    async def _read_output(self):
        """持续读取终端输出"""
        loop = asyncio.get_event_loop()
        while self.is_alive and self.master_fd is not None:
            try:
                # 使用 select 等待数据可读
                ready = await loop.run_in_executor(
                    None,
                    lambda: select.select([self.master_fd], [], [], 0.1)
                )
                if ready[0]:
                    data = os.read(self.master_fd, 4096)
                    if data:
                        text = data.decode("utf-8", errors="replace")
                        self._output_buffer += text

                        await event_bus.publish(SandboxEvent(
                            "terminal_output",
                            {
                                "session_id": self.session_id,
                                "data": text,
                            },
                            window_id=f"terminal_{self.session_id}"
                        ))
                    else:
                        # EOF
                        self.is_alive = False
                        break
            except OSError:
                self.is_alive = False
                break
            except Exception:
                await asyncio.sleep(0.1)

    async def write(self, data: str):
        """向终端写入数据"""
        if self.master_fd is not None and self.is_alive:
            os.write(self.master_fd, data.encode("utf-8"))

    async def execute_command(self, command: str) -> str:
        """执行命令并等待完成，返回输出"""
        if not self.is_alive:
            await self.start()

        # 清空缓冲区
        self._output_buffer = ""

        # 发送命令
        marker = f"__CMD_DONE_{id(command)}__"
        full_cmd = f"{command}; echo {marker}\n"
        await self.write(full_cmd)

        # 等待命令完成
        timeout = 30
        elapsed = 0
        while elapsed < timeout:
            await asyncio.sleep(0.2)
            elapsed += 0.2
            if marker in self._output_buffer:
                break

        # 提取输出（去掉命令本身和标记）
        output = self._output_buffer
        # 简单清理
        lines = output.split("\n")
        result_lines = []
        for line in lines:
            if marker in line:
                break
            if command in line and line.strip().startswith(command.strip()[:20]):
                continue
            result_lines.append(line)

        return "\n".join(result_lines).strip()

    async def stop(self):
        """停止终端会话"""
        self.is_alive = False
        if self._read_task:
            self._read_task.cancel()
        if self.pid:
            try:
                os.kill(self.pid, signal.SIGTERM)
                os.waitpid(self.pid, os.WNOHANG)
            except (ProcessLookupError, ChildProcessError):
                pass
        if self.master_fd is not None:
            try:
                os.close(self.master_fd)
            except OSError:
                pass

        await event_bus.publish(SandboxEvent(
            "terminal_stopped",
            {"session_id": self.session_id},
            window_id=f"terminal_{self.session_id}"
        ))

    def get_buffer(self) -> str:
        """获取当前输出缓冲区"""
        return self._output_buffer


class TerminalManager:
    """终端管理器 - 管理多个终端会话"""

    def __init__(self):
        self.sessions: Dict[str, TerminalSession] = {}
        self._counter = 0

    async def create_session(self, session_id: Optional[str] = None) -> TerminalSession:
        """创建新的终端会话"""
        if session_id is None:
            self._counter += 1
            session_id = f"term_{self._counter}"

        session = TerminalSession(session_id)
        await session.start()
        self.sessions[session_id] = session
        return session

    async def get_or_create(self, session_id: str = "default") -> TerminalSession:
        """获取或创建终端会话"""
        if session_id in self.sessions and self.sessions[session_id].is_alive:
            return self.sessions[session_id]
        return await self.create_session(session_id)

    async def close_session(self, session_id: str):
        """关闭终端会话"""
        if session_id in self.sessions:
            await self.sessions[session_id].stop()
            del self.sessions[session_id]

    async def close_all(self):
        """关闭所有终端会话"""
        for session in list(self.sessions.values()):
            await session.stop()
        self.sessions.clear()


# 全局终端管理器
terminal_manager = TerminalManager()
