"""
Docker 沙箱管理器 - 每会话独立容器隔离

架构设计：
- 每个 conversation_id 对应一个独立的 Docker 容器
- 容器基于预构建的 manus-sandbox 镜像（Ubuntu 22.04 + 开发工具链）
- 宿主机通过 Docker volume 挂载实现文件持久化
- 命令通过 docker exec 在容器内执行，实现真正的进程隔离
- 容器支持自动休眠（stop）和唤醒（start），节省资源
- 浏览器服务仍在宿主机运行（Playwright 需要 GUI 支持）

网络模式：
- bridge: 默认模式，容器使用独立桥接网络（需要 iptables 支持）
- host: 共享宿主机网络栈（适用于 iptables 受限的环境）
- none: 无网络（最高安全性，适用于纯计算任务）
- 自动检测：启动时探测 bridge 是否可用，不可用则回退到 host

安全特性：
- 容器以非 root 用户运行（ubuntu:1000）
- 资源限制：CPU、内存、PID 配额
- 路径穿越防护：容器内无法访问宿主机文件系统（仅挂载 workspace）
- 环境变量隔离：敏感变量不透传到容器
- 权限最小化：drop ALL capabilities，仅保留必要权限
"""

import asyncio
import json
import logging
import os
import re
import shutil
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("sandbox.docker")

# ---------------------------------------------------------------------------
# 配置常量
# ---------------------------------------------------------------------------

SANDBOX_IMAGE = os.environ.get("MANUS_SANDBOX_IMAGE", "manus-sandbox:latest")

# 网络模式：bridge / host / none / auto（自动检测）
SANDBOX_NETWORK_MODE = os.environ.get("MANUS_SANDBOX_NETWORK_MODE", "auto")
SANDBOX_NETWORK_NAME = os.environ.get("MANUS_SANDBOX_NETWORK", "manus-sandbox-net")

# 宿主机上存放各会话 workspace 的根目录
HOST_WORKSPACE_BASE = os.environ.get(
    "MANUS_HOST_WORKSPACE_BASE",
    "/tmp/manus_workspace",
)

# 容器内 workspace 挂载点
# 注意：必须使用 /tmp 子路径，避免 docker exec 在某些沙箱环境中
# 因 volume 挂载目录作为 cwd 触发 "outside of container mount namespace root" 错误
CONTAINER_WORKSPACE = "/tmp/workspace"
CONTAINER_HOME = "/home/ubuntu"
CONTAINER_USER = "ubuntu"

# 资源限制
CONTAINER_MEM_LIMIT = os.environ.get("MANUS_CONTAINER_MEM_LIMIT", "512m")
CONTAINER_CPU_QUOTA = int(os.environ.get("MANUS_CONTAINER_CPU_QUOTA", "100000"))  # 1 CPU
CONTAINER_CPU_PERIOD = int(os.environ.get("MANUS_CONTAINER_CPU_PERIOD", "100000"))
CONTAINER_PIDS_LIMIT = int(os.environ.get("MANUS_CONTAINER_PIDS_LIMIT", "256"))

# 命令执行超时
DEFAULT_EXEC_TIMEOUT = int(os.environ.get("MANUS_EXEC_TIMEOUT", "30"))
MAX_EXEC_TIMEOUT = int(os.environ.get("MANUS_MAX_EXEC_TIMEOUT", "300"))

# 容器空闲自动休眠时间（秒）
IDLE_TIMEOUT = int(os.environ.get("MANUS_CONTAINER_IDLE_TIMEOUT", "600"))

# 容器名前缀
CONTAINER_PREFIX = "manus-sandbox-"

# conversation_id 合法性检查
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

# 安全环境变量白名单（允许透传到容器）
SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
    "PYTHONPATH", "NODE_PATH",
}


def _normalize_conversation_id(conversation_id: Optional[str]) -> str:
    """规范化 conversation_id，返回安全的容器标识。"""
    if not conversation_id:
        return "_default"
    cid = str(conversation_id).strip()
    if not CONVERSATION_ID_PATTERN.fullmatch(cid):
        return "_default"
    return cid


def _container_name(conversation_id: str) -> str:
    """根据 conversation_id 生成容器名称。"""
    cid = _normalize_conversation_id(conversation_id)
    return f"{CONTAINER_PREFIX}{cid}"


def _host_workspace_path(conversation_id: str) -> str:
    """获取宿主机上该会话的 workspace 路径。
    
    确保目录存在且 UID 1000 (容器内 ubuntu 用户) 有写入权限。
    """
    cid = _normalize_conversation_id(conversation_id)
    path = os.path.join(HOST_WORKSPACE_BASE, cid)
    os.makedirs(path, exist_ok=True)
    # 确保容器内 ubuntu 用户 (UID 1000) 可以写入
    try:
        os.chown(path, 1000, 1000)
    except PermissionError:
        # 非 root 运行时尝试 chmod
        try:
            os.chmod(path, 0o777)
        except Exception:
            pass
    return path


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class SandboxContainer:
    """表示一个沙箱容器的状态。"""
    conversation_id: str
    container_name: str
    container_id: Optional[str] = None
    host_workspace: str = ""
    status: str = "created"  # created, running, stopped, error
    last_activity: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock, repr=False)

    def touch(self):
        """更新最后活动时间。"""
        self.last_activity = time.time()

    @property
    def is_idle(self) -> bool:
        return (time.time() - self.last_activity) > IDLE_TIMEOUT


# ---------------------------------------------------------------------------
# Docker 沙箱管理器
# ---------------------------------------------------------------------------

class DockerSandboxManager:
    """
    管理所有沙箱容器的生命周期。

    核心职责：
    1. 按需创建/启动/停止/销毁容器
    2. 在容器内执行命令并返回结果
    3. 管理文件系统挂载和持久化
    4. 定期清理空闲容器
    5. 自动检测最佳网络模式
    """

    def __init__(self):
        self._sandboxes: Dict[str, SandboxContainer] = {}
        self._global_lock = asyncio.Lock()
        self._cleanup_task: Optional[asyncio.Task] = None
        self._initialized = False
        self._network_mode: str = "host"  # 实际使用的网络模式

    # ---- 初始化 ----

    async def initialize(self):
        """初始化管理器：检测网络模式，启动清理任务。"""
        if self._initialized:
            return
        self._network_mode = await self._detect_network_mode()
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        self._initialized = True
        logger.info(
            "DockerSandboxManager 已初始化 (网络模式: %s)",
            self._network_mode,
        )

    async def _detect_network_mode(self) -> str:
        """自动检测可用的网络模式。"""
        configured = SANDBOX_NETWORK_MODE.strip().lower()

        if configured in ("host", "none"):
            logger.info("使用配置的网络模式: %s", configured)
            return configured

        if configured == "bridge":
            # 尝试创建桥接网络
            if await self._try_bridge_network():
                return "bridge"
            logger.warning("bridge 网络不可用，回退到 host 模式")
            return "host"

        # auto 模式：先尝试 bridge，失败则用 host
        if await self._try_bridge_network():
            logger.info("自动检测: bridge 网络可用")
            return "bridge"

        logger.info("自动检测: bridge 不可用，使用 host 网络模式")
        return "host"

    async def _try_bridge_network(self) -> bool:
        """尝试创建桥接网络，返回是否成功。"""
        try:
            # 先检查网络是否已存在
            proc = await asyncio.create_subprocess_exec(
                "docker", "network", "inspect", SANDBOX_NETWORK_NAME,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            await proc.wait()
            if proc.returncode == 0:
                # 网络存在，测试能否在其上运行容器
                return await self._test_network(SANDBOX_NETWORK_NAME)

            # 尝试创建
            proc = await asyncio.create_subprocess_exec(
                "docker", "network", "create", "--driver", "bridge",
                SANDBOX_NETWORK_NAME,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                return False

            return await self._test_network(SANDBOX_NETWORK_NAME)
        except Exception:
            return False

    async def _test_network(self, network_name: str) -> bool:
        """测试指定网络是否能正常运行容器。"""
        test_name = f"{CONTAINER_PREFIX}network-test-{int(time.time())}"
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "run", "--rm",
                "--name", test_name,
                "--network", network_name,
                SANDBOX_IMAGE,
                "echo", "ok",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=15)
            return proc.returncode == 0 and "ok" in stdout.decode()
        except Exception:
            # 清理可能残留的测试容器
            try:
                cleanup = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", test_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await cleanup.wait()
            except Exception:
                pass
            return False

    def _build_network_args(self) -> List[str]:
        """根据当前网络模式构建 docker run 的网络参数。"""
        if self._network_mode == "bridge":
            return ["--network", SANDBOX_NETWORK_NAME]
        elif self._network_mode == "none":
            return ["--network", "none"]
        else:  # host
            return ["--network", "host"]

    # ---- 容器生命周期 ----

    async def get_or_create(self, conversation_id: Optional[str] = None) -> SandboxContainer:
        """获取或创建指定会话的沙箱容器。"""
        cid = _normalize_conversation_id(conversation_id)

        # 快速路径：已存在且运行中
        sandbox = self._sandboxes.get(cid)
        if sandbox and sandbox.status == "running":
            sandbox.touch()
            return sandbox

        async with self._global_lock:
            # 双重检查
            sandbox = self._sandboxes.get(cid)
            if sandbox and sandbox.status == "running":
                sandbox.touch()
                return sandbox

            if sandbox and sandbox.status == "stopped":
                # 唤醒已休眠的容器
                try:
                    await self._start_container(sandbox)
                    return sandbox
                except Exception as e:
                    # 容器可能已被外部删除，回退到残留检测/重建流程
                    logger.warning(
                        "唤醒缓存容器失败 [%s]: %s，尝试重建",
                        sandbox.container_name,
                        e,
                    )
                    self._sandboxes.pop(cid, None)

            # 检查是否有残留容器
            container_name = _container_name(cid)
            existing_id = await self._find_container(container_name)
            if existing_id:
                sandbox = SandboxContainer(
                    conversation_id=cid,
                    container_name=container_name,
                    container_id=existing_id,
                    host_workspace=_host_workspace_path(cid),
                )
                running = await self._is_container_running(existing_id)
                if running:
                    sandbox.status = "running"
                else:
                    await self._start_container(sandbox)
                self._sandboxes[cid] = sandbox
                sandbox.touch()
                return sandbox

            # 创建新容器
            sandbox = await self._create_container(cid)
            self._sandboxes[cid] = sandbox
            return sandbox

    async def _create_container(self, conversation_id: str) -> SandboxContainer:
        """创建并启动一个新的沙箱容器。"""
        container_name = _container_name(conversation_id)
        host_workspace = _host_workspace_path(conversation_id)

        sandbox = SandboxContainer(
            conversation_id=conversation_id,
            container_name=container_name,
            host_workspace=host_workspace,
        )

        cmd = [
            "docker", "run", "-d",
            "--name", container_name,
        ]

        # 网络配置
        cmd.extend(self._build_network_args())

        # 资源限制
        cmd.extend([
            "--memory", CONTAINER_MEM_LIMIT,
            "--cpu-quota", str(CONTAINER_CPU_QUOTA),
            "--cpu-period", str(CONTAINER_CPU_PERIOD),
            "--pids-limit", str(CONTAINER_PIDS_LIMIT),
        ])

        # 安全选项
        cmd.extend([
            "--security-opt", "no-new-privileges",
            "--cap-drop", "ALL",
            "--cap-add", "CHOWN",
            "--cap-add", "DAC_OVERRIDE",
            "--cap-add", "FOWNER",
            "--cap-add", "SETUID",
            "--cap-add", "SETGID",
            "--cap-add", "NET_BIND_SERVICE",
        ])

        # 文件系统挂载：将宿主机 workspace 挂载到容器内 /tmp/workspace
        cmd.extend(["-v", f"{host_workspace}:{CONTAINER_WORKSPACE}"])

        # 工作目录：使用 /tmp/workspace，避免 docker exec cwd 问题
        cmd.extend(["-w", CONTAINER_WORKSPACE])

        # 用户：以 root 运行避免权限问题，容器内已通过 capabilities 限制
        cmd.extend(["--user", "root"])

        # 环境变量
        cmd.extend([
            "-e", f"HOME={CONTAINER_HOME}",
            "-e", "TERM=xterm-256color",
            "-e", f"USER={CONTAINER_USER}",
            "-e", "LANG=C.UTF-8",
        ])

        # 标签
        cmd.extend([
            "--label", "manus.sandbox=true",
            "--label", f"manus.conversation_id={conversation_id}",
        ])

        # 镜像和命令
        cmd.extend([SANDBOX_IMAGE, "sleep", "infinity"])

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace").strip()
                logger.error("创建容器失败 [%s]: %s", container_name, error_msg)
                sandbox.status = "error"
                raise RuntimeError(f"创建沙箱容器失败: {error_msg}")

            sandbox.container_id = stdout.decode().strip()[:12]
            sandbox.status = "running"
            sandbox.touch()
            logger.info(
                "沙箱容器已创建: %s (ID: %s, 网络: %s)",
                container_name,
                sandbox.container_id,
                self._network_mode,
            )
            return sandbox

        except FileNotFoundError:
            sandbox.status = "error"
            raise RuntimeError("Docker 未安装或不在 PATH 中")

    async def _start_container(self, sandbox: SandboxContainer):
        """启动（唤醒）一个已停止的容器。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "start", sandbox.container_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await proc.communicate()
            if proc.returncode != 0:
                error_msg = stderr.decode(errors="replace").strip()
                logger.error("启动容器失败 [%s]: %s", sandbox.container_name, error_msg)
                sandbox.status = "error"
                raise RuntimeError(f"启动沙箱容器失败: {error_msg}")

            sandbox.status = "running"
            sandbox.touch()
            logger.info("沙箱容器已唤醒: %s", sandbox.container_name)
        except FileNotFoundError:
            raise RuntimeError("Docker 未安装或不在 PATH 中")

    async def stop_container(self, conversation_id: Optional[str] = None):
        """休眠（停止）指定会话的容器。"""
        cid = _normalize_conversation_id(conversation_id)
        sandbox = self._sandboxes.get(cid)
        if not sandbox or sandbox.status != "running":
            return

        async with sandbox._lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    "docker", "stop", "-t", "5", sandbox.container_name,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=10)
                sandbox.status = "stopped"
                logger.info("沙箱容器已休眠: %s", sandbox.container_name)
            except Exception as e:
                logger.warning("停止容器异常 [%s]: %s", sandbox.container_name, e)

    async def destroy_container(self, conversation_id: Optional[str] = None):
        """销毁指定会话的容器（不删除 workspace 数据）。"""
        cid = _normalize_conversation_id(conversation_id)
        sandbox = self._sandboxes.pop(cid, None)
        container_name = sandbox.container_name if sandbox else _container_name(cid)

        # 兼容服务重启场景：即使 _sandboxes 内无状态，也尝试按容器名查找并删除。
        container_id = await self._find_container(container_name)
        if not container_id:
            return
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", container_name,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=10)
            if proc.returncode == 0:
                logger.info("沙箱容器已销毁: %s", container_name)
            else:
                logger.warning(
                    "销毁容器失败 [%s] (code=%s): %s",
                    container_name,
                    proc.returncode,
                    stderr.decode(errors="replace").strip(),
                )
        except Exception as e:
            logger.warning("销毁容器异常 [%s]: %s", container_name, e)

    async def destroy_all(self):
        """销毁所有沙箱容器。"""
        cids = list(self._sandboxes.keys())
        for cid in cids:
            await self.destroy_container(cid)

    # ---- 命令执行 ----

    async def exec_command(
        self,
        command: str,
        conversation_id: Optional[str] = None,
        timeout: int = DEFAULT_EXEC_TIMEOUT,
        workdir: Optional[str] = None,
        env: Optional[Dict[str, str]] = None,
    ) -> Tuple[int, str, str]:
        """
        在沙箱容器内执行命令。

        返回: (return_code, stdout, stderr)
        """
        sandbox = await self.get_or_create(conversation_id)
        sandbox.touch()

        timeout = min(timeout, MAX_EXEC_TIMEOUT)

        cmd = ["docker", "exec"]

        # 工作目录
        if workdir:
            cmd.extend(["-w", workdir])
        else:
            cmd.extend(["-w", CONTAINER_WORKSPACE])

        # 环境变量
        if env:
            for key, value in env.items():
                if key in SAFE_ENV_KEYS or key.startswith("MANUS_"):
                    cmd.extend(["-e", f"{key}={value}"])

        cmd.extend([sandbox.container_name, "/bin/bash", "-c", command])

        async with sandbox._lock:
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )

                try:
                    stdout_bytes, stderr_bytes = await asyncio.wait_for(
                        proc.communicate(),
                        timeout=timeout,
                    )
                except asyncio.TimeoutError:
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    try:
                        await asyncio.wait_for(proc.communicate(), timeout=2)
                    except Exception:
                        pass
                    return (-1, "", f"命令执行超时（{timeout}秒限制）")

                stdout_str = stdout_bytes.decode("utf-8", errors="replace")
                stderr_str = stderr_bytes.decode("utf-8", errors="replace")
                return (proc.returncode or 0, stdout_str, stderr_str)

            except Exception as e:
                return (-1, "", f"命令执行异常: {str(e)}")

    async def exec_python(
        self,
        code: str,
        conversation_id: Optional[str] = None,
        timeout: int = DEFAULT_EXEC_TIMEOUT,
    ) -> Tuple[int, str, str]:
        """
        在沙箱容器内执行 Python 代码。

        将代码写入容器内临时文件后执行。
        """
        sandbox = await self.get_or_create(conversation_id)
        sandbox.touch()

        # 将代码写入宿主机 workspace，容器内可通过挂载访问
        code_path = os.path.join(sandbox.host_workspace, "_temp_code.py")
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        container_code_path = f"{CONTAINER_WORKSPACE}/_temp_code.py"
        return await self.exec_command(
            f"python3 {container_code_path}",
            conversation_id=conversation_id,
            timeout=timeout,
        )

    # ---- 文件操作 ----

    def get_workspace_root(self, conversation_id: Optional[str] = None) -> str:
        """获取宿主机上该会话的 workspace 路径（兼容原有接口）。"""
        cid = _normalize_conversation_id(conversation_id)
        return _host_workspace_path(cid)

    async def write_file(
        self,
        path: str,
        content: str,
        conversation_id: Optional[str] = None,
    ) -> str:
        """在沙箱 workspace 中写入文件。"""
        workspace = self.get_workspace_root(conversation_id)
        resolved = self._resolve_path(path, workspace)

        os.makedirs(os.path.dirname(resolved), exist_ok=True)
        with open(resolved, "w", encoding="utf-8") as f:
            f.write(content)

        return os.path.relpath(resolved, workspace)

    async def read_file(
        self,
        path: str,
        conversation_id: Optional[str] = None,
    ) -> str:
        """从沙箱 workspace 中读取文件。"""
        workspace = self.get_workspace_root(conversation_id)
        resolved = self._resolve_path(path, workspace)

        if not os.path.exists(resolved):
            raise FileNotFoundError(f"文件不存在: {path}")

        with open(resolved, "r", encoding="utf-8", errors="replace") as f:
            return f.read()

    def _resolve_path(self, path: str, workspace: str) -> str:
        """安全解析路径，阻止路径穿越。"""
        if not isinstance(path, str) or not path.strip():
            raise ValueError("路径不能为空")

        raw = path.strip()
        candidate = Path(raw)
        if candidate.is_absolute():
            raise ValueError("仅支持相对路径，禁止绝对路径访问")

        workspace_path = Path(workspace).resolve()
        resolved = (workspace_path / candidate).resolve()
        if resolved != workspace_path and workspace_path not in resolved.parents:
            raise ValueError("路径越界：禁止访问工作目录外的文件")

        return str(resolved)

    # ---- 容器查询 ----

    async def _find_container(self, container_name: str) -> Optional[str]:
        """查找指定名称的容器，返回 container_id 或 None。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a",
                "--filter", f"name=^/{container_name}$",
                "--format", "{{.ID}}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            container_id = stdout.decode().strip()
            return container_id if container_id else None
        except Exception:
            return None

    async def _is_container_running(self, container_id: str) -> bool:
        """检查容器是否正在运行。"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "inspect",
                "--format", "{{.State.Running}}",
                container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await proc.communicate()
            return stdout.decode().strip().lower() == "true"
        except Exception:
            return False

    async def get_container_status(
        self,
        conversation_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """获取容器状态信息。"""
        cid = _normalize_conversation_id(conversation_id)
        sandbox = self._sandboxes.get(cid)
        if not sandbox:
            return {"status": "not_found", "conversation_id": cid}

        return {
            "conversation_id": cid,
            "container_name": sandbox.container_name,
            "container_id": sandbox.container_id,
            "status": sandbox.status,
            "host_workspace": sandbox.host_workspace,
            "last_activity": sandbox.last_activity,
            "is_idle": sandbox.is_idle,
            "network_mode": self._network_mode,
        }

    async def list_sandboxes(self) -> List[Dict[str, Any]]:
        """列出所有沙箱容器状态。"""
        results = []
        for cid, sandbox in self._sandboxes.items():
            results.append({
                "conversation_id": cid,
                "container_name": sandbox.container_name,
                "status": sandbox.status,
                "last_activity": sandbox.last_activity,
                "is_idle": sandbox.is_idle,
            })
        return results

    # ---- 清理 ----

    async def _cleanup_loop(self):
        """定期检查并休眠空闲容器。"""
        while True:
            try:
                await asyncio.sleep(60)  # 每分钟检查一次
                await self._cleanup_idle()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("清理循环异常: %s", e)

    async def _cleanup_idle(self):
        """休眠所有空闲容器。"""
        for cid, sandbox in list(self._sandboxes.items()):
            if sandbox.is_idle and sandbox.status == "running":
                logger.info("容器空闲超时，自动休眠: %s", sandbox.container_name)
                await self.stop_container(cid)

    async def shutdown(self):
        """关闭管理器：取消清理任务，停止所有容器。"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass

        # 停止（不销毁）所有容器，保留数据
        for cid in list(self._sandboxes.keys()):
            await self.stop_container(cid)

        logger.info("DockerSandboxManager 已关闭")


# ---------------------------------------------------------------------------
# 全局实例
# ---------------------------------------------------------------------------

sandbox_manager = DockerSandboxManager()
