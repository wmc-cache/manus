"""DeepSeek API 封装 - 兼容 OpenAI SDK"""
import os
import json
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any, Optional, Tuple
from openai import AsyncOpenAI


def _read_env_key_from_file(path: Path, key: str) -> str:
    if not path.exists():
        return ""
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            k, v = line.split("=", 1)
            if k.strip() != key:
                continue
            value = v.strip().strip('"').strip("'")
            if value:
                return value
    except Exception:
        return ""
    return ""


def _resolve_deepseek_api_key() -> str:
    # 优先使用显式环境变量
    env_key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if env_key:
        return env_key

    # 其次尝试后端本地 .env
    backend_env = Path(__file__).resolve().parents[1] / ".env"
    file_key = _read_env_key_from_file(backend_env, "DEEPSEEK_API_KEY")
    if file_key:
        return file_key

    # 最后兼容当前仓库结构：读取前端目录的 .env
    frontend_env = Path(__file__).resolve().parents[3] / "manus-frontend" / ".env"
    return _read_env_key_from_file(frontend_env, "DEEPSEEK_API_KEY")


# DeepSeek API 配置
DEEPSEEK_API_KEY = _resolve_deepseek_api_key()
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value >= minimum else default
    except ValueError:
        return default


# 大文件写入时，工具参数 JSON 体积会变大；默认提升输出 token 上限并支持环境变量覆盖。
DEEPSEEK_MAX_TOKENS = _read_int_env("DEEPSEEK_MAX_TOKENS", 8192, minimum=256)
# 部分模型可能限制更严格，若报 max_tokens 范围错误则自动回退到该值重试一次。
DEEPSEEK_MAX_TOKENS_FALLBACK = _read_int_env("DEEPSEEK_MAX_TOKENS_FALLBACK", 4096, minimum=256)

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


# Agent 系统提示词
SYSTEM_PROMPT = """你是 Manus，一个强大的 AI Agent 助手。你在一台虚拟计算机上工作，用户可以实时看到你的操作过程。

你拥有以下工具能力：
1. **web_search** - 搜索互联网获取最新信息
2. **wide_research** - 并行研究多个对象并自动产出汇总文件
3. **shell_exec** - 在终端中执行 shell 命令（用户可以在终端窗口看到）
4. **execute_code** - 执行 Python 代码（代码会显示在编辑器窗口中）
5. **browser_navigate** - 在浏览器中打开网页（用户可以在浏览器窗口看到截图）
6. **browser_get_content** - 获取当前浏览器页面的文本内容
7. **read_file** - 读取文件内容（文件会在编辑器窗口中显示）
8. **write_file** - 创建或写入文件（文件会在编辑器窗口中显示）

工作流程：
1. 分析用户的请求，理解任务目标
2. 制定执行计划（将复杂任务分解为步骤）
3. 逐步执行，调用需要的工具
4. 汇总结果，给出清晰的回答

注意事项：
- 对于复杂任务，先制定计划再执行
- 每次工具调用后，分析结果并决定下一步
- 给出清晰、有条理的最终回答
- 使用中文回复用户
- 优先使用 shell_exec 执行系统命令，使用 execute_code 执行 Python 代码
- 使用 browser_navigate 打开网页时，用户可以在浏览器窗口看到实时截图
- **重要：使用 write_file 和 read_file 时，路径请使用相对路径（如 "report.md"、"data/output.csv"），不要使用绝对路径。文件会自动保存到工作目录 /tmp/manus_workspace/ 中。**
- 写入文件后，文件会自动出现在用户的文件管理器窗口中
- **严禁在缺少参数时调用工具：write_file 必须同时提供 path 和 content；read_file 必须提供 path。若信息不足，先询问用户或先通过其他工具获取。**
- **写入长代码文件时每轮只调用 1 次 write_file；若内容过长，先写可运行最小版本再增量完善，避免参数 JSON 被截断。**
- **使用 wide_research 时，优先给出清晰的 query_template 和精简的 items 列表（例如 5~20 个），完成后读取汇总文件再给出结论。**
"""

# 工具定义（OpenAI Function Calling 格式）
TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取信息。用于查找最新资讯、事实验证、获取参考资料等。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "搜索关键词"
                    }
                },
                "required": ["query"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "wide_research",
            "description": "并行研究多个对象。会基于 query_template 对每个 item 执行搜索，并在工作目录 research/ 下产出分项结果和 summary.md。",
            "parameters": {
                "type": "object",
                "properties": {
                    "query_template": {
                        "type": "string",
                        "description": "查询模板，支持 {item} 占位符，如 '{item} 公司 2026 最新动态'"
                    },
                    "items": {
                        "type": "array",
                        "description": "待研究对象列表（字符串数组）",
                        "items": {
                            "type": "string"
                        }
                    }
                },
                "required": ["query_template", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "shell_exec",
            "description": "在终端中执行 shell 命令。用于系统操作、安装软件、文件管理等。命令会实时显示在用户的终端窗口中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "command": {
                        "type": "string",
                        "description": "要执行的 shell 命令"
                    }
                },
                "required": ["command"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_code",
            "description": "执行 Python 代码。用于数据处理、计算、生成图表等。代码会显示在编辑器窗口中。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "要执行的 Python 代码"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_navigate",
            "description": "在浏览器中打开指定 URL。用户可以在浏览器窗口看到页面截图。",
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "要打开的网页 URL"
                    }
                },
                "required": ["url"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_get_content",
            "description": "获取当前浏览器页面的文本内容。需要先使用 browser_navigate 打开网页。",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "read_file",
            "description": "读取指定路径的文件内容。使用相对路径（如 report.md），文件会在编辑器窗口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    }
                },
                "required": ["path"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "将内容写入指定路径的文件。使用相对路径（如 report.md），文件会自动保存到工作目录并在编辑器窗口中显示。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    }
]


def _parse_tool_arguments(raw_arguments: Any) -> Tuple[Dict[str, Any], Optional[str], str]:
    """解析工具参数，避免把解析失败静默吞成 {}。"""
    if isinstance(raw_arguments, dict):
        return raw_arguments, None, ""

    if raw_arguments is None:
        return {}, "参数为空，无法解析 JSON。", ""

    if not isinstance(raw_arguments, str):
        preview = str(raw_arguments)[:300]
        return {}, f"参数类型异常: {type(raw_arguments).__name__}。", preview

    text = raw_arguments.strip()
    if not text:
        return {}, "参数为空字符串，无法解析 JSON。", ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        preview = text[:300]
        return {}, f"参数 JSON 解析失败（位置 {e.pos}）: {e.msg}", preview

    if not isinstance(parsed, dict):
        return {}, f"参数 JSON 顶层必须是对象，当前为 {type(parsed).__name__}。", text[:300]

    return parsed, None, ""


def _normalize_tool_calls(raw_tool_calls: Any) -> List[Dict[str, Any]]:
    tool_calls: List[Dict[str, Any]] = []
    if not raw_tool_calls:
        return tool_calls

    for tc in raw_tool_calls:
        function = getattr(tc, "function", None)
        raw_arguments = getattr(function, "arguments", None)
        parsed_args, parse_error, preview = _parse_tool_arguments(raw_arguments)

        item: Dict[str, Any] = {
            "id": getattr(tc, "id", "") or "",
            "name": getattr(function, "name", "") or "",
            "arguments": parsed_args,
        }
        if parse_error:
            item["parse_error"] = parse_error
            if preview:
                item["raw_arguments_preview"] = preview

        tool_calls.append(item)

    return tool_calls


def _select_tools(allowed_tool_names: Optional[List[str]]) -> List[Dict[str, Any]]:
    """按名称筛选可用工具；为空时返回全部工具。"""
    if allowed_tool_names is None:
        return TOOLS
    allow = {name.strip() for name in allowed_tool_names if isinstance(name, str) and name.strip()}
    if not allow:
        return []
    return [
        tool
        for tool in TOOLS
        if tool.get("function", {}).get("name") in allow
    ]


async def _create_completion(kwargs: Dict[str, Any]):
    """创建补全请求，必要时对 max_tokens 做一次回退重试。"""
    try:
        return await client.chat.completions.create(**kwargs)
    except Exception as e:
        err_text = str(e).lower()
        if (
            kwargs.get("max_tokens") != DEEPSEEK_MAX_TOKENS_FALLBACK
            and "max_tokens" in err_text
        ):
            retry_kwargs = dict(kwargs)
            retry_kwargs["max_tokens"] = DEEPSEEK_MAX_TOKENS_FALLBACK
            return await client.chat.completions.create(**retry_kwargs)
        raise


async def chat_completion_stream(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        yield {
            "type": "error",
            "data": "DEEPSEEK_API_KEY 未配置，请在环境变量或 .env 中设置后重启后端。"
        }
        return

    try:
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion(kwargs)

        current_content = ""
        current_tool_calls = {}

        async for chunk in response:
            delta = chunk.choices[0].delta if chunk.choices else None
            if not delta:
                continue

            finish_reason = chunk.choices[0].finish_reason

            if delta.content:
                current_content += delta.content
                yield {"type": "content", "data": delta.content}

            if delta.tool_calls:
                for tc in delta.tool_calls:
                    idx = tc.index
                    if idx not in current_tool_calls:
                        current_tool_calls[idx] = {"id": tc.id or "", "name": "", "arguments": ""}
                    if tc.id:
                        current_tool_calls[idx]["id"] = tc.id
                    if tc.function:
                        if tc.function.name:
                            current_tool_calls[idx]["name"] = tc.function.name
                        if tc.function.arguments:
                            current_tool_calls[idx]["arguments"] += tc.function.arguments

            if finish_reason == "tool_calls":
                for idx, tc in current_tool_calls.items():
                    args, parse_error, preview = _parse_tool_arguments(tc["arguments"])
                    payload = {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": args,
                    }
                    if parse_error:
                        payload["parse_error"] = parse_error
                        if preview:
                            payload["raw_arguments_preview"] = preview
                    yield {"type": "tool_call", "data": payload}
            elif finish_reason == "stop":
                tool_calls_payload: List[Dict[str, Any]] = []
                for tc in current_tool_calls.values():
                    args, parse_error, preview = _parse_tool_arguments(tc["arguments"])
                    item: Dict[str, Any] = {
                        "id": tc["id"],
                        "name": tc["name"],
                        "arguments": args,
                    }
                    if parse_error:
                        item["parse_error"] = parse_error
                        if preview:
                            item["raw_arguments_preview"] = preview
                    tool_calls_payload.append(item)
                yield {"type": "done", "data": {"content": current_content, "tool_calls": tool_calls_payload}}

    except Exception as e:
        yield {"type": "error", "data": str(e)}


async def chat_completion(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """非流式调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        return {
            "content": "调用 LLM 时出错: DEEPSEEK_API_KEY 未配置，请在环境变量或 .env 中设置后重启后端。",
            "tool_calls": []
        }

    try:
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion(kwargs)
        choice = response.choices[0]

        result = {
            "content": choice.message.content or "",
            "tool_calls": _normalize_tool_calls(choice.message.tool_calls),
        }
        if choice.finish_reason:
            result["finish_reason"] = choice.finish_reason

        return result

    except Exception as e:
        return {"content": f"调用 LLM 时出错: {str(e)}", "tool_calls": []}
