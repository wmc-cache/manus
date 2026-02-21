"""Agent 工具系统 - 定义和执行各种工具，联动计算机窗口（支持会话隔离）"""
import os
import sys
import json
import re
import asyncio
import tempfile
import traceback
from typing import Any, Dict, Optional

from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.browser import browser_service
from sandbox.filesystem import notify_file_change

WORKSPACE_BASE = "/tmp/manus_workspace"

# 当前执行上下文中的 conversation_id（由 execute_tool 设置）
_current_conversation_id: Optional[str] = None


def _get_workspace(conversation_id: Optional[str] = None) -> str:
    """获取当前会话的 workspace 目录"""
    cid = conversation_id or _current_conversation_id
    if cid:
        path = os.path.join(WORKSPACE_BASE, cid)
    else:
        path = os.path.join(WORKSPACE_BASE, "_default")
    os.makedirs(path, exist_ok=True)
    return path


def _publish_event(event_type: str, data: dict, window_id: Optional[str] = None):
    """创建带 conversation_id 的事件"""
    return SandboxEvent(
        event_type, data,
        window_id=window_id,
        conversation_id=_current_conversation_id,
    )


# ============ 工具：网页搜索（Tavily API）============
TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")

async def web_search(query: str) -> str:
    """优先使用 Tavily；无 key 时回退到 DuckDuckGo 搜索"""
    try:
        if TAVILY_API_KEY:
            from tavily import TavilyClient

            client = TavilyClient(api_key=TAVILY_API_KEY)
            loop = asyncio.get_event_loop()
            response = await loop.run_in_executor(
                None,
                lambda: client.search(
                    query=query,
                    max_results=5,
                    search_depth="basic",
                    include_answer=True,
                )
            )

            results = response.get("results", [])
            answer = response.get("answer", "")

            if not results and not answer:
                return f"搜索 \"{query}\" 未找到相关结果。请尝试换个关键词。"

            output = f"搜索 \"{query}\" 的结果：\n\n"

            if answer:
                output += f"**AI 摘要：** {answer}\n\n---\n\n"

            for i, r in enumerate(results, 1):
                title = r.get("title", "无标题")
                url = r.get("url", "")
                snippet = r.get("content", "")
                score = r.get("score", 0)
                output += f"{i}. **{title}** (相关度: {score:.2f})\n"
                output += f"   链接: {url}\n"
                output += f"   摘要: {snippet[:300]}\n\n"

            return output

        from duckduckgo_search import DDGS

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: list(DDGS().text(query, max_results=5))
        )

        if not results:
            return f"搜索 \"{query}\" 未找到相关结果。请尝试换个关键词。"

        output = f"搜索 \"{query}\" 的结果（DuckDuckGo）：\n\n"
        for i, r in enumerate(results, 1):
            title = r.get("title", "无标题")
            url = r.get("href", "")
            snippet = r.get("body", "")
            output += f"{i}. **{title}**\n"
            output += f"   链接: {url}\n"
            output += f"   摘要: {snippet[:300]}\n\n"
        return output
    except Exception as e:
        return f"搜索出错: {str(e)}。我可以基于已有知识为你提供相关信息。"


# ============ 工具：Shell 命令执行（联动终端窗口）============
def _is_background_shell_command(command: str) -> bool:
    """判断是否为显式后台命令（以单个 & 结尾，例如 `python app.py &`）"""
    text = command.strip()
    return bool(re.search(r"(?<!&)&\s*$", text))


async def shell_exec(command: str) -> str:
    """在终端中执行 shell 命令，实时显示在计算机窗口"""
    workspace = _get_workspace()
    try:
        # 发布命令执行事件到终端窗口
        await event_bus.publish(_publish_event(
            "terminal_command",
            {"command": command, "session_id": "default"},
            window_id="terminal_default"
        ))

        # 后台命令（末尾带 &）不应阻塞等待输出，否则容易卡住。
        if _is_background_shell_command(command):
            process = await asyncio.create_subprocess_shell(
                command,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                cwd=workspace,
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

            result = "后台命令已启动。你可以继续后续步骤（如打开浏览器访问服务）。"
            await event_bus.publish(_publish_event(
                "terminal_output",
                {"session_id": "default", "data": f"{result}\n"},
                window_id="terminal_default"
            ))
            return result

        # 使用 subprocess 执行命令
        process = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )
        except asyncio.TimeoutError:
            # 防止二次等待卡死：杀进程后再次等待也加超时保护。
            try:
                process.kill()
            except ProcessLookupError:
                pass

            try:
                await asyncio.wait_for(process.communicate(), timeout=2)
            except Exception:
                pass
            return "命令执行超时（30秒限制）"

        output = ""
        if stdout:
            output += stdout.decode('utf-8', errors='replace')
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace')
            if stderr_text.strip():
                output += "\n" + stderr_text

        result = output.strip() if output.strip() else "命令执行成功（无输出）"

        # 发布执行结果到终端窗口
        await event_bus.publish(_publish_event(
            "terminal_output",
            {"session_id": "default", "data": f"{result}\n"},
            window_id="terminal_default"
        ))

        if len(result) > 5000:
            result = result[:5000] + "\n... [输出被截断]"

        return result

    except Exception as e:
        return f"命令执行出错: {str(e)}"


# ============ 工具：代码执行（联动终端 + 编辑器窗口）============
async def execute_code(code: str) -> str:
    """在沙箱中执行 Python 代码，联动计算机窗口"""
    workspace = _get_workspace()
    try:
        code_path = os.path.join(workspace, "_temp_code.py")

        with open(code_path, 'w', encoding='utf-8') as f:
            f.write(code)

        # 通知编辑器窗口打开文件
        await event_bus.publish(_publish_event(
            "file_opened",
            {
                "path": "_temp_code.py",
                "name": "_temp_code.py",
                "content": code,
                "language": "python",
            },
            window_id="editor"
        ))

        # 发布命令到终端窗口
        await event_bus.publish(_publish_event(
            "terminal_command",
            {"command": f"python3 _temp_code.py", "session_id": "default"},
            window_id="terminal_default"
        ))

        # 异步执行代码
        process = await asyncio.create_subprocess_exec(
            sys.executable, code_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=30
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return "代码执行超时（30秒限制）"

        output = ""
        if stdout:
            output += stdout.decode('utf-8', errors='replace')
        if stderr:
            output += "\n[STDERR]\n" + stderr.decode('utf-8', errors='replace')

        if not output.strip():
            output = "代码执行成功（无输出）"

        if len(output) > 3000:
            output = output[:3000] + "\n... [输出被截断]"

        # 发布执行结果到终端窗口
        await event_bus.publish(_publish_event(
            "terminal_output",
            {"session_id": "default", "data": f"{output}\n"},
            window_id="terminal_default"
        ))

        # 通知文件变更
        await notify_file_change("_temp_code.py", "created", _current_conversation_id)

        # 清理临时文件
        try:
            os.unlink(code_path)
        except OSError:
            pass

        return output

    except Exception as e:
        return f"代码执行出错: {str(e)}\n{traceback.format_exc()}"


# ============ 工具：浏览器导航（联动浏览器窗口）============
async def browser_navigate(url: str) -> str:
    """在浏览器中打开指定 URL"""
    result = await browser_service.navigate(url, conversation_id=_current_conversation_id)
    if "error" in result:
        return f"浏览器导航失败: {result['error']}"
    return f"已打开网页: {result.get('title', '')} ({url})\n状态码: {result.get('status', 0)}"


# ============ 工具：浏览器截图 ============
async def browser_screenshot() -> str:
    """获取当前浏览器页面截图"""
    result = await browser_service.screenshot(conversation_id=_current_conversation_id)
    return f"已截取页面截图: {result.get('title', '')} ({result.get('url', '')})"


# ============ 工具：浏览器获取内容 ============
async def browser_get_content() -> str:
    """获取当前浏览器页面的文本内容"""
    content = await browser_service.get_content()
    return content


# ============ 工具：读取文件（联动编辑器窗口）============
async def read_file(path: str) -> str:
    """读取文件内容"""
    workspace = _get_workspace()
    try:
        if not os.path.isabs(path):
            path = os.path.join(workspace, path)

        if not os.path.exists(path):
            return f"文件不存在: {path}"

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # 通知编辑器窗口
        rel_path = os.path.relpath(path, workspace) if path.startswith(workspace) else path
        await event_bus.publish(_publish_event(
            "file_opened",
            {
                "path": rel_path,
                "name": os.path.basename(path),
                "content": content[:10000],
                "language": _get_lang(path),
            },
            window_id="editor"
        ))

        if len(content) > 5000:
            content = content[:5000] + "\n... [内容被截断]"

        return f"文件 {path} 的内容：\n\n{content}"

    except Exception as e:
        return f"读取文件出错: {str(e)}"


# ============ 工具：写入文件（联动编辑器 + 文件管理器窗口）============
async def write_file(path: str, content: str) -> str:
    """写入文件"""
    workspace = _get_workspace()
    try:
        if not isinstance(path, str) or not path.strip():
            return "写入文件出错: 参数 path 不能为空。请使用相对路径，例如 report.md。"

        if not isinstance(content, str):
            return "写入文件出错: 参数 content 必须是字符串。"

        if not os.path.isabs(path):
            path = os.path.join(workspace, path)

        if os.path.isdir(path):
            return f"写入文件出错: path 指向目录而不是文件: {path}"

        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        existed = os.path.exists(path)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        # 通知编辑器窗口
        rel_path = os.path.relpath(path, workspace) if path.startswith(workspace) else path
        await event_bus.publish(_publish_event(
            "file_opened",
            {
                "path": rel_path,
                "name": os.path.basename(path),
                "content": content[:10000],
                "language": _get_lang(path),
            },
            window_id="editor"
        ))

        # 通知文件变更
        await notify_file_change(rel_path, "modified" if existed else "created", _current_conversation_id)

        return f"文件已成功写入: {path} ({len(content)} 字符)"

    except Exception as e:
        return f"写入文件出错: {str(e)}"


def _get_lang(path: str) -> str:
    """获取文件语言"""
    ext = os.path.splitext(path)[1].lower()
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".html": "html", ".css": "css", ".json": "json",
        ".md": "markdown", ".sh": "shell", ".yaml": "yaml",
        ".sql": "sql", ".xml": "xml",
    }
    return lang_map.get(ext, "plaintext")


# ============ 工具注册表 ============
TOOL_REGISTRY = {
    "web_search": {
        "func": web_search,
        "extract_args": lambda args: {"query": args.get("query")},
        "required_keys": ["query"],
        "non_empty_keys": ["query"],
        "string_keys": ["query"],
        "usage_hint": '示例: {"query": "最新 AI 行业动态"}',
    },
    "shell_exec": {
        "func": shell_exec,
        "extract_args": lambda args: {"command": args.get("command")},
        "required_keys": ["command"],
        "non_empty_keys": ["command"],
        "string_keys": ["command"],
        "usage_hint": '示例: {"command": "ls -la"}',
    },
    "execute_code": {
        "func": execute_code,
        "extract_args": lambda args: {"code": args.get("code")},
        "required_keys": ["code"],
        "non_empty_keys": ["code"],
        "string_keys": ["code"],
        "usage_hint": '示例: {"code": "print(123)"}',
    },
    "browser_navigate": {
        "func": browser_navigate,
        "extract_args": lambda args: {"url": args.get("url")},
        "required_keys": ["url"],
        "non_empty_keys": ["url"],
        "string_keys": ["url"],
        "usage_hint": '示例: {"url": "https://example.com"}',
    },
    "browser_screenshot": {
        "func": browser_screenshot,
        "extract_args": lambda args: {},
    },
    "browser_get_content": {
        "func": browser_get_content,
        "extract_args": lambda args: {},
    },
    "read_file": {
        "func": read_file,
        "extract_args": lambda args: {"path": args.get("path")},
        "required_keys": ["path"],
        "non_empty_keys": ["path"],
        "string_keys": ["path"],
        "usage_hint": '示例: {"path": "report.md"}',
    },
    "write_file": {
        "func": write_file,
        "extract_args": lambda args: {
            "path": args.get("path"),
            "content": args.get("content")
        },
        "required_keys": ["path", "content"],
        "non_empty_keys": ["path"],
        "string_keys": ["path", "content"],
        "usage_hint": '示例: {"path": "report.md", "content": "# 报告"}',
    },
}


async def execute_tool(name: str, arguments: Dict[str, Any], conversation_id: Optional[str] = None) -> str:
    """执行指定工具（带会话隔离）"""
    global _current_conversation_id
    _current_conversation_id = conversation_id

    try:
        if name not in TOOL_REGISTRY:
            return f"未知工具: {name}"

        if not isinstance(arguments, dict):
            raise ValueError(f"工具 `{name}` 参数格式错误: 需要 JSON 对象。")

        tool = TOOL_REGISTRY[name]
        kwargs = tool["extract_args"](arguments)

        required_keys = tool.get("required_keys", [])
        non_empty_keys = tool.get("non_empty_keys", [])
        string_keys = tool.get("string_keys", [])
        usage_hint = tool.get("usage_hint", "")

        missing_keys = [k for k in required_keys if k not in kwargs or kwargs.get(k) is None]
        if missing_keys:
            hint = f" {usage_hint}" if usage_hint else ""
            raise ValueError(f"工具 `{name}` 缺少必填参数: {', '.join(missing_keys)}。{hint}".strip())

        empty_keys = []
        for k in non_empty_keys:
            v = kwargs.get(k)
            if not isinstance(v, str) or not v.strip():
                empty_keys.append(k)
        if empty_keys:
            hint = f" {usage_hint}" if usage_hint else ""
            raise ValueError(f"工具 `{name}` 参数不能为空: {', '.join(empty_keys)}。{hint}".strip())

        wrong_type_keys = []
        for k in string_keys:
            v = kwargs.get(k)
            if v is not None and not isinstance(v, str):
                wrong_type_keys.append(k)
        if wrong_type_keys:
            hint = f" {usage_hint}" if usage_hint else ""
            raise ValueError(f"工具 `{name}` 参数类型错误(应为字符串): {', '.join(wrong_type_keys)}。{hint}".strip())

        result = await tool["func"](**kwargs)
        return result
    finally:
        _current_conversation_id = None
