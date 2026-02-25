"""
Docker 沙箱工具适配层

将原有的进程级工具执行（subprocess）透明地替换为 Docker 容器内执行。
通过统一的适配接口，使上层工具模块（tools.py / tools_extended.py）
无需大幅修改即可运行在容器化沙箱中。

设计原则：
- 向后兼容：当 Docker 不可用时自动回退到进程级执行
- 最小侵入：上层工具只需替换执行函数引用
- 事件联动：执行结果仍通过 event_bus 推送到前端
"""

import asyncio
import logging
import os
from typing import Any, Dict, Optional, Tuple

from sandbox.docker_sandbox import (
    sandbox_manager,
    CONTAINER_WORKSPACE,
    HOST_WORKSPACE_BASE,
)
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.filesystem import notify_file_change, get_workspace_root

logger = logging.getLogger("sandbox.docker_adapter")

# 是否启用 Docker 沙箱（可通过环境变量控制）
DOCKER_SANDBOX_ENABLED = os.environ.get(
    "MANUS_DOCKER_SANDBOX", "true"
).strip().lower() in ("true", "1", "yes")


async def _ensure_initialized():
    """确保 Docker 沙箱管理器已初始化。"""
    if not sandbox_manager._initialized:
        await sandbox_manager.initialize()


def _is_docker_available() -> bool:
    """检查 Docker 是否可用。"""
    return DOCKER_SANDBOX_ENABLED


# ---------------------------------------------------------------------------
# Shell 命令执行适配
# ---------------------------------------------------------------------------

async def docker_shell_exec(
    command: str,
    conversation_id: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    在 Docker 沙箱中执行 shell 命令。

    替代原有的 subprocess 执行方式，提供容器级隔离。
    """
    if not _is_docker_available():
        return await _fallback_shell_exec(command, conversation_id, timeout)

    await _ensure_initialized()

    # 发布命令开始事件
    await event_bus.publish(SandboxEvent(
        "terminal_command",
        {"command": command, "session_id": "default"},
        window_id="terminal_default",
        conversation_id=conversation_id,
    ))

    # 检查是否为后台命令
    if command.strip().endswith("&"):
        return_code, stdout, stderr = await sandbox_manager.exec_command(
            f"nohup {command} > /dev/null 2>&1",
            conversation_id=conversation_id,
            timeout=5,
        )
        result = "后台命令已在沙箱容器中启动。"
        await event_bus.publish(SandboxEvent(
            "terminal_output",
            {"session_id": "default", "data": f"{result}\n"},
            window_id="terminal_default",
            conversation_id=conversation_id,
        ))
        return result

    # 正常命令执行
    return_code, stdout, stderr = await sandbox_manager.exec_command(
        command,
        conversation_id=conversation_id,
        timeout=timeout,
    )

    # 组装输出
    output = ""
    if stdout:
        output += stdout
    if stderr and stderr.strip():
        output += ("\n" if output else "") + stderr

    result = output.strip() if output.strip() else "命令执行成功（无输出）"

    # 超时特殊处理
    if return_code == -1 and "超时" in stderr:
        result = stderr

    # 发布执行结果
    await event_bus.publish(SandboxEvent(
        "terminal_output",
        {"session_id": "default", "data": f"{result}\n"},
        window_id="terminal_default",
        conversation_id=conversation_id,
    ))

    # 截断过长输出
    if len(result) > 5000:
        result = result[:5000] + "\n... [输出被截断]"

    return result


# ---------------------------------------------------------------------------
# Python 代码执行适配
# ---------------------------------------------------------------------------

async def docker_execute_code(
    code: str,
    conversation_id: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """
    在 Docker 沙箱中执行 Python 代码。

    替代原有的本地 subprocess 执行方式。
    """
    if not _is_docker_available():
        return await _fallback_execute_code(code, conversation_id, timeout)

    await _ensure_initialized()

    # 发布代码到编辑器窗口
    await event_bus.publish(SandboxEvent(
        "file_opened",
        {
            "path": "_temp_code.py",
            "name": "_temp_code.py",
            "content": code[:10000],
            "language": "python",
        },
        window_id="editor",
        conversation_id=conversation_id,
    ))

    # 在容器内执行
    return_code, stdout, stderr = await sandbox_manager.exec_python(
        code,
        conversation_id=conversation_id,
        timeout=timeout,
    )

    # 组装输出
    output = ""
    if stdout:
        output += stdout
    if stderr and stderr.strip():
        output += ("\n" if output else "") + stderr

    result = output.strip() if output.strip() else "代码执行成功（无输出）"

    if return_code == -1 and "超时" in stderr:
        result = stderr

    # 发布到终端窗口
    await event_bus.publish(SandboxEvent(
        "terminal_output",
        {"session_id": "default", "data": f"$ python3 _temp_code.py\n{result}\n"},
        window_id="terminal_default",
        conversation_id=conversation_id,
    ))

    if len(result) > 5000:
        result = result[:5000] + "\n... [输出被截断]"

    return result


# ---------------------------------------------------------------------------
# 文件操作适配（通过宿主机 volume 挂载直接操作，无需进入容器）
# ---------------------------------------------------------------------------

def docker_get_workspace_root(conversation_id: Optional[str] = None) -> str:
    """
    获取 workspace 根目录。

    Docker 模式下返回宿主机上的挂载路径（与容器内路径同步）。
    非 Docker 模式下回退到原有逻辑。
    """
    if _is_docker_available():
        return sandbox_manager.get_workspace_root(conversation_id)
    return get_workspace_root(conversation_id)


# ---------------------------------------------------------------------------
# 回退实现（Docker 不可用时使用原有进程级方式）
# ---------------------------------------------------------------------------

async def _fallback_shell_exec(
    command: str,
    conversation_id: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """回退到进程级 shell 执行。"""
    workspace = get_workspace_root(conversation_id)

    safe_env_keys = {
        "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
        "PYTHONPATH", "NODE_PATH",
    }
    env = {k: v for k, v in os.environ.items() if k in safe_env_keys}
    env["HOME"] = workspace
    env["PWD"] = workspace
    env.setdefault("TERM", "xterm-256color")

    try:
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=timeout,
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            return f"命令执行超时（{timeout}秒限制）"

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace")
            if stderr_text.strip():
                output += "\n" + stderr_text

        return output.strip() if output.strip() else "命令执行成功（无输出）"
    except Exception as e:
        return f"命令执行出错: {str(e)}"


async def _fallback_execute_code(
    code: str,
    conversation_id: Optional[str] = None,
    timeout: int = 30,
) -> str:
    """回退到进程级 Python 代码执行。"""
    workspace = get_workspace_root(conversation_id)
    code_path = os.path.join(workspace, "_temp_code.py")

    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)

    return await _fallback_shell_exec(
        f"python3 {code_path}",
        conversation_id=conversation_id,
        timeout=timeout,
    )


# ---------------------------------------------------------------------------
# 容器状态 API
# ---------------------------------------------------------------------------

async def get_sandbox_status(
    conversation_id: Optional[str] = None,
) -> Dict[str, Any]:
    """获取沙箱状态（供 API 层调用）。"""
    if not _is_docker_available():
        workspace = get_workspace_root(conversation_id)
        return {
            "mode": "process",
            "workspace": workspace,
            "docker_enabled": False,
        }

    await _ensure_initialized()
    status = await sandbox_manager.get_container_status(conversation_id)
    status["mode"] = "docker"
    status["docker_enabled"] = True
    return status


async def list_all_sandboxes() -> list:
    """列出所有沙箱状态。"""
    if not _is_docker_available():
        return [{"mode": "process", "docker_enabled": False}]

    await _ensure_initialized()
    return await sandbox_manager.list_sandboxes()
