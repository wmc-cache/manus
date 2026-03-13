"""
mcp-execution: 代码与命令执行 MCP 服务
提供 shell 命令执行、Python 代码执行、端口暴露等能力
"""

import asyncio
import logging
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Optional

import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-shared"))
from mcp_base import MCPService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

WORKSPACE_BASE = os.environ.get("MANUS_WORKSPACE_BASE", "/tmp/manus_workspace")
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
SHELL_TIMEOUT = int(os.environ.get("MCP_SHELL_TIMEOUT", "30"))
CODE_TIMEOUT  = int(os.environ.get("MCP_CODE_TIMEOUT", "30"))
OUTPUT_MAX    = int(os.environ.get("MCP_OUTPUT_MAX", "5000"))

# 允许传递给子进程的安全环境变量
SAFE_ENV_KEYS = {
    "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ",
    "SHELL", "USER", "LOGNAME", "TMPDIR", "TMP", "TEMP",
    "http_proxy", "https_proxy", "no_proxy",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY",
}


def _get_workspace(conversation_id: Optional[str]) -> str:
    if conversation_id and CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        path = os.path.join(WORKSPACE_BASE, conversation_id)
    else:
        path = os.path.join(WORKSPACE_BASE, "_default")
    os.makedirs(path, exist_ok=True)
    return path


def _build_sandbox_env(workspace: str) -> dict:
    env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}
    env["HOME"] = workspace
    env["PWD"] = workspace
    return env


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def shell_exec(command: str, conversation_id: Optional[str] = None) -> str:
    """在沙箱工作区中执行 shell 命令"""
    workspace = _get_workspace(conversation_id)
    sandbox_env = _build_sandbox_env(workspace)

    # 检测后台命令（以 & 结尾）
    stripped = command.strip()
    is_background = stripped.endswith("&")

    try:
        if is_background:
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=workspace,
                env=sandbox_env,
                start_new_session=True,
            )
            try:
                await asyncio.wait_for(process.wait(), timeout=3)
            except asyncio.TimeoutError:
                try:
                    process.kill()
                    await asyncio.wait_for(process.wait(), timeout=1)
                except Exception:
                    pass
                return "后台命令启动失败：启动器未在预期时间内退出。"
            return "后台命令已启动，可继续后续步骤。"

        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=sandbox_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=SHELL_TIMEOUT
            )
        except asyncio.TimeoutError:
            try:
                process.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(process.communicate(), timeout=2)
            except Exception:
                pass
            return f"命令执行超时（{SHELL_TIMEOUT}秒限制）"

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            stderr_text = stderr.decode("utf-8", errors="replace")
            if stderr_text.strip():
                output += "\n" + stderr_text

        result = output.strip() if output.strip() else "命令执行成功（无输出）"
        if len(result) > OUTPUT_MAX:
            result = result[:OUTPUT_MAX] + "\n... [输出被截断]"
        return result

    except Exception as e:
        return f"命令执行出错: {e}"


async def execute_code(code: str, conversation_id: Optional[str] = None) -> str:
    """在沙箱工作区中执行 Python 代码"""
    workspace = _get_workspace(conversation_id)
    sandbox_env = _build_sandbox_env(workspace)

    try:
        code_path = os.path.join(workspace, "_temp_code.py")
        with open(code_path, "w", encoding="utf-8") as f:
            f.write(code)

        process = await asyncio.create_subprocess_exec(
            sys.executable, code_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=sandbox_env,
        )
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=CODE_TIMEOUT
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return f"代码执行超时（{CODE_TIMEOUT}秒限制）"

        output = ""
        if stdout:
            output += stdout.decode("utf-8", errors="replace")
        if stderr:
            output += "\n[STDERR]\n" + stderr.decode("utf-8", errors="replace")

        if not output.strip():
            output = "代码执行成功（无输出）"
        if len(output) > OUTPUT_MAX:
            output = output[:OUTPUT_MAX] + "\n... [输出被截断]"

        try:
            os.unlink(code_path)
        except OSError:
            pass

        return output

    except Exception as e:
        return f"代码执行出错: {e}\n{traceback.format_exc()}"


async def expose_port(port: int, label: str = "", conversation_id: Optional[str] = None) -> str:
    """暴露工作区内的端口，生成可访问的公网链接"""
    if not isinstance(port, int) or port <= 0 or port > 65535:
        return f"无效端口: {port}"

    public_base = os.environ.get("PUBLIC_BASE_URL", "").rstrip("/")
    if public_base:
        url = f"{public_base}/proxy/{port}/"
        label_str = f"（{label}）" if label else ""
        return (
            f"端口 {port} 已暴露{label_str}。\n"
            f"访问链接: {url}\n"
            f"注意：此链接通过反向代理访问，服务需绑定 0.0.0.0:{port}"
        )
    return (
        f"端口 {port} 服务已在本地启动。\n"
        f"如需外部访问，请配置 PUBLIC_BASE_URL 环境变量。"
    )


# ---------------------------------------------------------------------------
# 服务构建
# ---------------------------------------------------------------------------

service = MCPService(
    name="mcp-execution",
    version="1.0.0",
    description="Manus 代码与命令执行服务，提供沙箱内的 shell 命令执行、Python 代码运行和端口暴露能力",
)

service.register_tool(
    name="shell_exec",
    description="在沙箱工作区中执行 shell 命令，支持后台运行（命令末尾加 &）",
    parameters={
        "type": "object",
        "properties": {
            "command": {"type": "string", "description": "要执行的 shell 命令"},
        },
        "required": ["command"],
    },
    func=shell_exec,
)

service.register_tool(
    name="execute_code",
    description="在沙箱工作区中执行 Python 代码并返回输出",
    parameters={
        "type": "object",
        "properties": {
            "code": {"type": "string", "description": "要执行的 Python 代码"},
        },
        "required": ["code"],
    },
    func=execute_code,
)

service.register_tool(
    name="expose_port",
    description="暴露沙箱内运行的 Web 服务端口，生成可从外部访问的链接",
    parameters={
        "type": "object",
        "properties": {
            "port":  {"type": "integer", "description": "要暴露的端口号（1-65535）"},
            "label": {"type": "string",  "description": "服务的可读名称（可选）"},
        },
        "required": ["port"],
    },
    func=expose_port,
)

app = service.app

if __name__ == "__main__":
    port = int(os.environ.get("MCP_EXECUTION_PORT", "8102"))
    uvicorn.run(app, host="0.0.0.0", port=port)
