"""
tools_mcp.py — MCP 工具执行适配器

本模块提供与原 tools.py 完全兼容的 execute_tool 接口，
但底层通过 MCP 客户端将工具调用路由到对应的 MCP 微服务。

使用方式：
  在 agent/core.py 中将
    from agent.tools import execute_tool, TOOL_REGISTRY
  替换为
    from agent.tools_mcp import execute_tool, get_tool_definitions
  即可完成迁移，无需修改其他逻辑。

降级策略：
  若环境变量 MANUS_USE_MCP=false（默认），则回退到原有本地工具执行。
  若 MANUS_USE_MCP=true，则使用 MCP 服务执行工具。
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# 是否启用 MCP 模式（通过环境变量控制，便于灰度切换）
_USE_MCP = os.environ.get("MANUS_USE_MCP", "true").strip().lower() in ("1", "true", "yes")
_BROWSER_TOOL_NAMES = {
    "browser_navigate",
    "browser_screenshot",
    "browser_click",
    "browser_input",
    "browser_scroll",
}

# 需要在本地执行的工具列表（这些工具依赖本地 Playwright/browser_service，不适合走 MCP 微服务）
_LOCAL_ONLY_TOOLS = {
    "browser_navigate", "browser_screenshot", "browser_get_content",
    "browser_click", "browser_input", "browser_scroll",
    # 以下工具依赖本地 Agent 上下文（如 sub_agent、conversation_store 等）
    "spawn_sub_agents", "wide_research", "data_analysis",
}

# ---------------------------------------------------------------------------
# MCP 工具定义（与 LLM 的 Function Calling 格式兼容）
# 这些定义用于在 Agent 的系统提示中告知 LLM 可用工具
# ---------------------------------------------------------------------------

MCP_TOOL_DEFINITIONS: List[Dict] = [
    # ---- 文件系统 ----
    {
        "name": "read_file",
        "description": "读取工作区内指定文件的内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "文件的相对路径，例如 report.md"},
            },
            "required": ["path"],
        },
    },
    {
        "name": "write_file",
        "description": "将内容写入工作区内的文件（覆盖写入，自动创建目录）",
        "parameters": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "文件的相对路径"},
                "content": {"type": "string", "description": "要写入的文件内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "append_file",
        "description": "向工作区内的文件末尾追加内容",
        "parameters": {
            "type": "object",
            "properties": {
                "path":    {"type": "string", "description": "文件的相对路径"},
                "content": {"type": "string", "description": "要追加的内容"},
            },
            "required": ["path", "content"],
        },
    },
    {
        "name": "edit_file",
        "description": "精确替换文件中的指定内容片段",
        "parameters": {
            "type": "object",
            "properties": {
                "path":        {"type": "string", "description": "文件的相对路径"},
                "old_content": {"type": "string", "description": "要被替换的原始内容（必须与文件中完全一致）"},
                "new_content": {"type": "string", "description": "替换后的新内容"},
            },
            "required": ["path", "old_content", "new_content"],
        },
    },
    {
        "name": "list_files",
        "description": "列出工作区内指定目录下的文件和子目录树",
        "parameters": {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "要列出的目录路径，默认为根目录 '.'"},
            },
        },
    },
    {
        "name": "find_files",
        "description": "在工作区内按文件名模式（glob）查找文件",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "文件名匹配模式，例如 '*.py'"},
                "path":    {"type": "string", "description": "搜索起始目录，默认为 '.'"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "grep_files",
        "description": "在工作区内按正则表达式搜索文件内容",
        "parameters": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string", "description": "正则表达式搜索模式"},
                "path":    {"type": "string", "description": "搜索起始目录，默认为 '.'"},
            },
            "required": ["pattern"],
        },
    },
    # ---- 代码执行 ----
    {
        "name": "shell_exec",
        "description": "在沙箱工作区中执行 shell 命令，支持后台运行（命令末尾加 &）",
        "parameters": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "execute_code",
        "description": "在沙箱工作区中执行 Python 代码并返回输出",
        "parameters": {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的 Python 代码"},
            },
            "required": ["code"],
        },
    },
    {
        "name": "expose_port",
        "description": "暴露沙箱内运行的 Web 服务端口，生成可从外部访问的链接",
        "parameters": {
            "type": "object",
            "properties": {
                "port":  {"type": "integer", "description": "要暴露的端口号（1-65535）"},
                "label": {"type": "string",  "description": "服务的可读名称（可选）"},
            },
            "required": ["port"],
        },
    },
    # ---- 浏览器 ----
    {
        "name": "browser_navigate",
        "description": "导航浏览器到指定 URL",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {"type": "string", "description": "要访问的完整 URL，包含协议前缀（如 https://）"},
            },
            "required": ["url"],
        },
    },
    {
        "name": "browser_screenshot",
        "description": "对当前浏览器页面进行截图",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_get_content",
        "description": "获取当前浏览器页面的文本内容",
        "parameters": {"type": "object", "properties": {}},
    },
    {
        "name": "browser_click",
        "description": "点击页面中的元素（通过 CSS 选择器或坐标）",
        "parameters": {
            "type": "object",
            "properties": {
                "selector": {"type": "string", "description": "CSS 选择器，例如 '#submit-btn'"},
                "x": {"type": "number", "description": "点击位置的 X 坐标（像素）"},
                "y": {"type": "number", "description": "点击位置的 Y 坐标（像素）"},
            },
        },
    },
    {
        "name": "browser_input",
        "description": "在指定输入框或当前焦点元素中输入文本",
        "parameters": {
            "type": "object",
            "properties": {
                "text":     {"type": "string", "description": "要输入的文本内容"},
                "selector": {"type": "string", "description": "输入框的 CSS 选择器；不传时输入到当前焦点元素"},
                "submit":   {"type": "boolean", "description": "输入后是否按 Enter 提交"},
            },
            "required": ["text"],
        },
    },
    {
        "name": "browser_scroll",
        "description": "滚动当前页面",
        "parameters": {
            "type": "object",
            "properties": {
                "direction": {
                    "type": "string",
                    "description": "滚动方向",
                    "enum": ["up", "down", "top", "bottom"],
                },
                "amount": {"type": "integer", "description": "滚动像素数（默认 500）"},
            },
        },
    },
    # ---- 搜索研究 ----
    {
        "name": "web_search",
        "description": "使用 Tavily API 搜索互联网，获取最新信息",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "搜索关键词或问题"},
            },
            "required": ["query"],
        },
    },
    {
        "name": "wide_research",
        "description": "对多个条目并发执行网页搜索并汇总结果，适合批量信息收集",
        "parameters": {
            "type": "object",
            "properties": {
                "query_template": {
                    "type": "string",
                    "description": "搜索查询模板，使用 {item} 作为占位符，例如 '{item} 公司 2026 最新动态'",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要研究的条目列表，例如 ['OpenAI', 'Anthropic']",
                },
            },
            "required": ["query_template", "items"],
        },
    },
    {
        "name": "spawn_sub_agents",
        "description": "对多个条目并发执行独立的研究子任务，并可指定汇总目标",
        "parameters": {
            "type": "object",
            "properties": {
                "task_template": {
                    "type": "string",
                    "description": "子任务描述模板，使用 {item} 作为占位符",
                },
                "items": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "要处理的条目列表",
                },
                "reduce_goal": {
                    "type": "string",
                    "description": "汇总目标描述（可选）",
                },
                "max_concurrency": {
                    "type": "integer",
                    "description": "最大并发数（默认 5）",
                },
                "max_items": {
                    "type": "integer",
                    "description": "最大处理条目数（默认 20）",
                },
            },
            "required": ["task_template", "items"],
        },
    },
    {
        "name": "data_analysis",
        "description": "对提供的 JSON 数据或文本数据进行结构化分析",
        "parameters": {
            "type": "object",
            "properties": {
                "data": {"type": "string", "description": "要分析的数据（JSON 字符串或文本）"},
                "task": {"type": "string", "description": "分析目标描述"},
            },
            "required": ["data", "task"],
        },
    },
]


def get_tool_definitions() -> List[Dict]:
    """返回所有 MCP 工具的定义列表（供 LLM 使用）"""
    return MCP_TOOL_DEFINITIONS


async def execute_tool(
    name: str,
    arguments: Dict[str, Any],
    conversation_id: Optional[str] = None,
) -> str:
    """
    执行指定工具。
    - 若 MANUS_USE_MCP=true，通过 MCP 客户端调用对应微服务。
    - 否则，回退到原有本地工具执行（兼容模式）。
    """
    # 浏览器工具和依赖本地上下文的工具始终走本地执行
    if name in _LOCAL_ONLY_TOOLS:
        logger.debug("[tools_mcp] 工具 %s 走本地执行路径", name)
        return await _execute_local(name, arguments, conversation_id)

    if _USE_MCP:
        return await _execute_via_mcp(name, arguments, conversation_id)
    else:
        return await _execute_local(name, arguments, conversation_id)


async def _execute_via_mcp(
    name: str,
    arguments: Dict[str, Any],
    conversation_id: Optional[str],
) -> str:
    """通过 MCP 客户端调用工具"""
    try:
        # 动态导入，避免循环依赖
        import sys
        import os
        mcp_shared_path = os.path.join(
            os.path.dirname(__file__), "..", "..", "..",
            "mcp-services", "mcp-shared"
        )
        if mcp_shared_path not in sys.path:
            sys.path.insert(0, mcp_shared_path)

        from mcp_client import mcp_client
        if name == "browser_navigate":
            await _publish_browser_event(
                "browser_navigating",
                {"url": arguments.get("url", "")},
                conversation_id,
            )
        result = await mcp_client.execute_tool(name, arguments, conversation_id)
        if name in _BROWSER_TOOL_NAMES:
            return await _handle_browser_tool_result(name, arguments, result, conversation_id)
        return result
    except ImportError:
        logger.warning("[tools_mcp] 无法导入 mcp_client，回退到本地执行")
        return await _execute_local(name, arguments, conversation_id)
    except Exception as e:
        if name in _BROWSER_TOOL_NAMES:
            await _publish_browser_event(
                "browser_error",
                {"action": name, "error": str(e)},
                conversation_id,
            )
        return f"MCP 工具执行失败: {e}"


async def _publish_browser_event(
    event_type: str,
    data: Dict[str, Any],
    conversation_id: Optional[str],
) -> None:
    """向前端浏览器窗口广播兼容旧实现的 sandbox 事件。"""
    try:
        from sandbox.event_bus import SandboxEvent, event_bus

        await event_bus.publish(
            SandboxEvent(
                event_type,
                data,
                window_id="browser",
                conversation_id=conversation_id,
            )
        )
    except Exception:
        logger.exception("[tools_mcp] 发布浏览器事件失败: %s", event_type)


async def _handle_browser_tool_result(
    name: str,
    arguments: Dict[str, Any],
    raw_result: str,
    conversation_id: Optional[str],
) -> str:
    """解析 mcp-browser 的 JSON 结果，并同步为前端可消费的 sandbox 事件。"""
    try:
        payload = json.loads(raw_result)
    except json.JSONDecodeError:
        return raw_result

    if not isinstance(payload, dict):
        return raw_result

    if not payload.get("ok", False):
        await _publish_browser_event(
            "browser_error",
            {
                "action": payload.get("action") or name,
                "error": payload.get("error") or payload.get("message") or "unknown error",
                "url": payload.get("url") or arguments.get("url", ""),
            },
            conversation_id,
        )
        return str(payload.get("message") or raw_result)

    event_data = {
        "url": payload.get("url", ""),
        "title": payload.get("title", ""),
        "screenshot": payload.get("screenshot", ""),
    }
    if payload.get("status") is not None:
        event_data["status"] = payload["status"]

    if name == "browser_navigate":
        await _publish_browser_event("browser_navigated", event_data, conversation_id)
    elif name == "browser_screenshot":
        await _publish_browser_event("browser_screenshot", event_data, conversation_id)
    elif name == "browser_click":
        click_event_data = dict(event_data)
        if "selector" in payload:
            click_event_data["selector"] = payload["selector"]
        if "x" in payload:
            click_event_data["x"] = payload["x"]
        if "y" in payload:
            click_event_data["y"] = payload["y"]
        await _publish_browser_event("browser_clicked", click_event_data, conversation_id)
    else:
        await _publish_browser_event("browser_screenshot", event_data, conversation_id)

    message = str(payload.get("message") or raw_result)
    screenshot = payload.get("screenshot")
    mime_type = str(payload.get("mime_type") or "image/jpeg")
    if name == "browser_screenshot" and screenshot:
        return f"{message}\n[IMAGE:data:{mime_type};base64,{screenshot}]"
    return message


async def _execute_local(
    name: str,
    arguments: Dict[str, Any],
    conversation_id: Optional[str],
) -> str:
    """回退到原有本地工具执行"""
    try:
        from agent.tools import execute_tool as _local_execute
        return await _local_execute(name, arguments, conversation_id)
    except Exception as e:
        return f"本地工具执行失败: {e}"
