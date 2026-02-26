"""
DeepSeek API 封装 - 兼容 OpenAI SDK

Improvements:
1. Enhanced system prompt with structured instructions
2. Extended tool definitions (edit_file, list_files, data_analysis, etc.)
3. Retry with exponential backoff for transient errors
4. Request-level timeout protection
5. Token usage tracking
"""
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import AsyncGenerator, List, Dict, Any, Optional, Tuple

from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


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


def _resolve_multi_key(keys: tuple[str, ...], default: str = "") -> str:
    for key_name in keys:
        env_value = os.environ.get(key_name, "").strip()
        if env_value:
            return env_value

    backend_env = Path(__file__).resolve().parents[1] / ".env"
    for key_name in keys:
        file_value = _read_env_key_from_file(backend_env, key_name)
        if file_value:
            return file_value

    frontend_env = Path(__file__).resolve().parents[3] / "manus-frontend" / ".env"
    for key_name in keys:
        file_value = _read_env_key_from_file(frontend_env, key_name)
        if file_value:
            return file_value

    return default


def _resolve_deepseek_api_key() -> str:
    # Claude-compatible env first, then legacy DeepSeek env.
    return _resolve_multi_key(("CLAUDE_API_KEY", "DEEPSEEK_API_KEY"), default="")


# DeepSeek API 配置
DEEPSEEK_API_KEY = _resolve_deepseek_api_key()
DEEPSEEK_BASE_URL = _resolve_multi_key(
    ("CLAUDE_BASE_URL", "DEEPSEEK_BASE_URL"),
    default="https://api.deepseek.com",
)
DEEPSEEK_MODEL = _resolve_multi_key(
    ("CLAUDE_MODEL", "DEEPSEEK_MODEL"),
    default="deepseek-chat",
)


def _read_int_env(name: str, default: int, minimum: int = 1) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value >= minimum else default
    except ValueError:
        return default


DEEPSEEK_MAX_TOKENS = _read_int_env("DEEPSEEK_MAX_TOKENS", 8192, minimum=256)
DEEPSEEK_MAX_TOKENS_FALLBACK = _read_int_env("DEEPSEEK_MAX_TOKENS_FALLBACK", 4096, minimum=256)
MAX_RETRIES = _read_int_env("DEEPSEEK_MAX_RETRIES", 3, minimum=1)
REQUEST_TIMEOUT = _read_int_env("DEEPSEEK_REQUEST_TIMEOUT", 120, minimum=10)

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
    timeout=REQUEST_TIMEOUT,
)


# ============ Enhanced System Prompt ============
# Import enhanced system prompt from dedicated module, with fallback
try:
    from llm.system_prompt import ENHANCED_SYSTEM_PROMPT
    SYSTEM_PROMPT = ENHANCED_SYSTEM_PROMPT
except ImportError:
    SYSTEM_PROMPT = "你是 Manus，一个强大的通用 AI Agent 助手。请使用工具完成用户任务。"


# ============ Tool Definitions (OpenAI Function Calling format) ============

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": "搜索互联网获取信息。用于查找最新资讯、新闻动态、事实验证、获取参考资料等。涉及时效信息时应优先使用本工具。",
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
                        "items": {"type": "string"}
                    }
                },
                "required": ["query_template", "items"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "spawn_sub_agents",
            "description": "启动多个轻量子代理并行执行同质任务。每个子代理在 multi_agent/agents/<agent_id>/ 产出 task/observation/result，并在 multi_agent/reduce_summary.md 做汇总。支持自动重试失败的子代理。",
            "parameters": {
                "type": "object",
                "properties": {
                    "task_template": {
                        "type": "string",
                        "description": "子代理任务模板，支持 {item} 占位符"
                    },
                    "items": {
                        "type": "array",
                        "description": "待并行处理对象列表（字符串数组）",
                        "items": {"type": "string"}
                    },
                    "reduce_goal": {
                        "type": "string",
                        "description": "可选，reduce 阶段的汇总目标说明"
                    },
                    "max_concurrency": {
                        "type": "integer",
                        "description": "可选，子代理并发数（正整数）"
                    },
                    "max_items": {
                        "type": "integer",
                        "description": "可选，本次最多处理条目数（正整数）"
                    },
                    "max_iterations": {
                        "type": "integer",
                        "description": "可选，单个子代理最大循环轮数（正整数）"
                    }
                },
                "required": ["task_template", "items"]
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
            "description": "在浏览器中打开指定 URL。仅在需要网页交互（登录、点击、输入、滚动等）时使用；纯信息检索优先 web_search。",
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
            "name": "browser_click",
            "description": "在浏览器页面上点击指定坐标。需要先用 browser_navigate 打开页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {
                        "type": "number",
                        "description": "点击位置的 X 坐标（像素）"
                    },
                    "y": {
                        "type": "number",
                        "description": "点击位置的 Y 坐标（像素）"
                    }
                },
                "required": ["x", "y"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_input",
            "description": "在浏览器当前聚焦的输入框中输入文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "要输入的文本"
                    },
                    "submit": {
                        "type": "boolean",
                        "description": "输入后是否按回车提交（默认 false）"
                    }
                },
                "required": ["text"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "browser_scroll",
            "description": "滚动浏览器页面。",
            "parameters": {
                "type": "object",
                "properties": {
                    "direction": {
                        "type": "string",
                        "enum": ["up", "down"],
                        "description": "滚动方向"
                    },
                    "amount": {
                        "type": "number",
                        "description": "滚动像素数（默认 300）"
                    }
                },
                "required": ["direction"]
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
                        "description": "文件路径（相对路径）"
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
                        "description": "文件路径（相对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要写入的内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "edit_file",
            "description": "对文件进行精确的查找替换编辑，比重写整个文件更高效。每个编辑包含 find（要查找的文本）和 replace（替换文本）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    },
                    "edits": {
                        "type": "array",
                        "description": "编辑操作列表",
                        "items": {
                            "type": "object",
                            "properties": {
                                "find": {
                                    "type": "string",
                                    "description": "要查找的文本"
                                },
                                "replace": {
                                    "type": "string",
                                    "description": "替换文本"
                                },
                                "all": {
                                    "type": "boolean",
                                    "description": "是否替换所有匹配（默认 false）"
                                }
                            },
                            "required": ["find", "replace"]
                        }
                    }
                },
                "required": ["path", "edits"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "append_file",
            "description": "向文件末尾追加内容，适合增量写入。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "文件路径（相对路径）"
                    },
                    "content": {
                        "type": "string",
                        "description": "要追加的内容"
                    }
                },
                "required": ["path", "content"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "list_files",
            "description": "列出目录内容，以树形结构显示文件和子目录。",
            "parameters": {
                "type": "object",
                "properties": {
                    "path": {
                        "type": "string",
                        "description": "目录路径（相对路径，默认 '.'）"
                    },
                    "max_depth": {
                        "type": "integer",
                        "description": "最大递归深度（默认 3）"
                    }
                },
                "required": []
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "data_analysis",
            "description": "执行 Python 数据分析代码。自动导入 pandas、numpy、matplotlib、seaborn，图表自动保存为 PNG。",
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {
                        "type": "string",
                        "description": "Python 数据分析代码"
                    },
                    "description": {
                        "type": "string",
                        "description": "分析目标描述（可选）"
                    }
                },
                "required": ["code"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "find_files",
            "description": "使用 glob 模式匹配查找文件。支持 ** 递归匹配。例如: **/*.py 查找所有 Python 文件, src/**/*.ts 查找 src 下所有 TypeScript 文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "pattern": {
                        "type": "string",
                        "description": "Glob 匹配模式（如 **/*.py, *.md, src/**/*.ts）"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起始目录（相对路径，默认 '.'）"
                    },
                    "max_results": {
                        "type": "integer",
                        "description": "最大返回结果数（默认 50）"
                    }
                },
                "required": ["pattern"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "grep_files",
            "description": "使用正则表达式搜索文件内容。支持上下文行显示。结果按文件修改时间倒序排列。适合在项目中查找特定代码、配置或文本。",
            "parameters": {
                "type": "object",
                "properties": {
                    "regex": {
                        "type": "string",
                        "description": "正则表达式搜索模式"
                    },
                    "scope": {
                        "type": "string",
                        "description": "文件范围的 glob 模式（如 **/*.py 只搜索 Python 文件，默认 **/*）"
                    },
                    "path": {
                        "type": "string",
                        "description": "搜索起始目录（相对路径，默认 '.'）"
                    },
                    "leading": {
                        "type": "integer",
                        "description": "匹配行前的上下文行数（默认 0）"
                    },
                    "trailing": {
                        "type": "integer",
                        "description": "匹配行后的上下文行数（默认 0）"
                    }
                },
                "required": ["regex"]
            }
        }
    },
]


# ============ Parsing Utilities ============

def _parse_tool_arguments(raw_arguments: Any) -> Tuple[Dict[str, Any], Optional[str], str]:
    """解析工具参数，避免把解析失败静默吞成 {}。"""
    if isinstance(raw_arguments, dict):
        return raw_arguments, None, ""

    if raw_arguments is None:
        # 一些模型对无参工具会返回 null / 空值，按空对象处理。
        return {}, None, ""

    if not isinstance(raw_arguments, str):
        preview = str(raw_arguments)[:300]
        return {}, f"参数类型异常: {type(raw_arguments).__name__}。", preview

    text = raw_arguments.strip()
    if not text:
        # 一些模型对无参工具会返回空字符串，按空对象处理。
        return {}, None, ""

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError as e:
        preview = text[:300]
        return {}, f"参数 JSON 解析失败（位置 {e.pos}）: {e.msg}", preview

    if not isinstance(parsed, dict):
        if parsed is None:
            return {}, None, ""
        return {}, f"参数解析后不是 JSON 对象，而是 {type(parsed).__name__}。", str(parsed)[:300]

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


def _sanitize_messages_for_api(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    修复消息序列，避免 `role=tool` 与 `assistant.tool_calls` 失配导致 400。

    规则：
    1. 丢弃无法匹配的孤立 tool 消息
    2. 若 assistant 的 tool_calls 在后续未完整闭合，则移除该 assistant 的 tool_calls 字段
    3. 过滤缺少 id/name 的无效 tool_call
    """
    sanitized: List[Dict[str, Any]] = []
    pending_tool_ids: set[str] = set()
    pending_assistant_index: Optional[int] = None

    dropped_orphan_tools = 0
    dropped_invalid_tool_calls = 0
    stripped_unclosed_tool_calls = 0

    def _strip_pending_assistant_tool_calls() -> None:
        nonlocal pending_tool_ids, pending_assistant_index, stripped_unclosed_tool_calls
        if pending_assistant_index is None or not pending_tool_ids:
            pending_tool_ids = set()
            pending_assistant_index = None
            return
        item = dict(sanitized[pending_assistant_index])
        if "tool_calls" in item:
            item.pop("tool_calls", None)
            stripped_unclosed_tool_calls += 1
        if item.get("content") is None:
            item["content"] = ""
        sanitized[pending_assistant_index] = item
        pending_tool_ids = set()
        pending_assistant_index = None

    for raw in messages:
        if not isinstance(raw, dict):
            continue

        role = raw.get("role")

        if role == "assistant":
            tool_calls = raw.get("tool_calls")
            if isinstance(tool_calls, list) and tool_calls:
                _strip_pending_assistant_tool_calls()

                cleaned_tool_calls: List[Dict[str, Any]] = []
                cleaned_ids: set[str] = set()
                for tc in tool_calls:
                    if not isinstance(tc, dict):
                        dropped_invalid_tool_calls += 1
                        continue
                    tc_id = str(tc.get("id", "")).strip()
                    func = tc.get("function")
                    name = ""
                    if isinstance(func, dict):
                        name = str(func.get("name", "")).strip()
                    if not tc_id or not name:
                        dropped_invalid_tool_calls += 1
                        continue
                    arguments = "{}"
                    if isinstance(func, dict):
                        raw_arguments = func.get("arguments")
                        if isinstance(raw_arguments, str) and raw_arguments.strip():
                            arguments = raw_arguments
                        elif raw_arguments is not None:
                            arguments = json.dumps(raw_arguments, ensure_ascii=False)
                    cleaned_tool_calls.append({
                        "id": tc_id,
                        "type": "function",
                        "function": {
                            "name": name,
                            "arguments": arguments,
                        },
                    })
                    cleaned_ids.add(tc_id)

                item = dict(raw)
                if cleaned_tool_calls:
                    item["tool_calls"] = cleaned_tool_calls
                    if item.get("content") is None:
                        item["content"] = ""
                    sanitized.append(item)
                    pending_tool_ids = cleaned_ids
                    pending_assistant_index = len(sanitized) - 1
                else:
                    item.pop("tool_calls", None)
                    if item.get("content") is None:
                        item["content"] = ""
                    sanitized.append(item)
                    pending_tool_ids = set()
                    pending_assistant_index = None
                continue

            _strip_pending_assistant_tool_calls()
            item = dict(raw)
            item.pop("tool_calls", None)
            if item.get("content") is None:
                item["content"] = ""
            sanitized.append(item)
            continue

        if role == "tool":
            if not pending_tool_ids:
                dropped_orphan_tools += 1
                continue

            tool_call_id = str(raw.get("tool_call_id", "")).strip()
            if not tool_call_id:
                if len(pending_tool_ids) == 1:
                    tool_call_id = next(iter(pending_tool_ids))
                else:
                    dropped_orphan_tools += 1
                    continue

            if tool_call_id not in pending_tool_ids:
                dropped_orphan_tools += 1
                continue

            item = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "content": raw.get("content") or "",
            }
            sanitized.append(item)
            pending_tool_ids.remove(tool_call_id)
            if not pending_tool_ids:
                pending_assistant_index = None
            continue

        _strip_pending_assistant_tool_calls()

        if role not in {"system", "user"}:
            continue
        item = dict(raw)
        if item.get("content") is None:
            item["content"] = ""
        sanitized.append(item)

    _strip_pending_assistant_tool_calls()

    if dropped_orphan_tools or dropped_invalid_tool_calls or stripped_unclosed_tool_calls:
        logger.warning(
            "Sanitized LLM messages: dropped_orphan_tools=%d dropped_invalid_tool_calls=%d stripped_unclosed_tool_calls=%d input=%d output=%d",
            dropped_orphan_tools,
            dropped_invalid_tool_calls,
            stripped_unclosed_tool_calls,
            len(messages),
            len(sanitized),
        )

    return sanitized


# ============ Retry Logic ============

_RETRYABLE_ERRORS = (
    "rate_limit",
    "timeout",
    "server_error",
    "503",
    "502",
    "429",
    "connection",
    "overloaded",
)


def _is_retryable(error: Exception) -> bool:
    """Check if an error is transient and retryable."""
    err_text = str(error).lower()
    return any(keyword in err_text for keyword in _RETRYABLE_ERRORS)


async def _create_completion_with_retry(kwargs: Dict[str, Any], max_retries: int = MAX_RETRIES):
    """Create completion with retry and max_tokens fallback."""
    last_error = None

    for attempt in range(max_retries):
        try:
            return await client.chat.completions.create(**kwargs)
        except Exception as e:
            last_error = e
            err_text = str(e).lower()

            # max_tokens fallback (try once)
            if (
                attempt == 0
                and kwargs.get("max_tokens") != DEEPSEEK_MAX_TOKENS_FALLBACK
                and "max_tokens" in err_text
            ):
                retry_kwargs = dict(kwargs)
                retry_kwargs["max_tokens"] = DEEPSEEK_MAX_TOKENS_FALLBACK
                try:
                    return await client.chat.completions.create(**retry_kwargs)
                except Exception as e2:
                    last_error = e2
                    if not _is_retryable(e2):
                        raise

            # Retry for transient errors
            if _is_retryable(e) and attempt < max_retries - 1:
                delay = 2 ** attempt  # Exponential backoff: 1s, 2s, 4s
                logger.warning(
                    "LLM request failed (attempt %d/%d), retrying in %ds: %s",
                    attempt + 1, max_retries, delay, str(e)[:200],
                )
                await asyncio.sleep(delay)
                continue

            raise

    raise last_error  # type: ignore


# ============ Token Usage Tracking ============

_total_prompt_tokens = 0
_total_completion_tokens = 0


def _track_usage(response):
    """Track token usage from API response."""
    global _total_prompt_tokens, _total_completion_tokens
    usage = getattr(response, "usage", None)
    if usage:
        _total_prompt_tokens += getattr(usage, "prompt_tokens", 0) or 0
        _total_completion_tokens += getattr(usage, "completion_tokens", 0) or 0


def get_token_usage() -> Dict[str, int]:
    """Return cumulative token usage."""
    return {
        "prompt_tokens": _total_prompt_tokens,
        "completion_tokens": _total_completion_tokens,
        "total_tokens": _total_prompt_tokens + _total_completion_tokens,
    }


# ============ Stream Completion ============

async def chat_completion_stream(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式调用 DeepSeek API"""
    if not DEEPSEEK_API_KEY:
        yield {
            "type": "error",
            "data": "LLM API Key 未配置，请在环境变量或 .env 中设置 CLAUDE_API_KEY 或 DEEPSEEK_API_KEY 后重启后端。"
        }
        return

    try:
        safe_messages = _sanitize_messages_for_api(messages)
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + safe_messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion_with_retry(kwargs)

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


# ============ Non-Stream Completion ============

async def chat_completion(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
    allowed_tool_names: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """非流式调用 DeepSeek API（带重试）"""
    if not DEEPSEEK_API_KEY:
        return {
            "content": "调用 LLM 时出错: 未配置 API Key。请在环境变量或 .env 中设置 CLAUDE_API_KEY 或 DEEPSEEK_API_KEY 后重启后端。",
            "tool_calls": []
        }

    try:
        safe_messages = _sanitize_messages_for_api(messages)
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + safe_messages,
            "temperature": 0.7,
            "max_tokens": DEEPSEEK_MAX_TOKENS,
        }
        selected_tools = _select_tools(allowed_tool_names)
        if use_tools and selected_tools:
            kwargs["tools"] = selected_tools
            kwargs["tool_choice"] = "auto"

        response = await _create_completion_with_retry(kwargs)
        _track_usage(response)

        choice = response.choices[0]

        result = {
            "content": choice.message.content or "",
            "tool_calls": _normalize_tool_calls(choice.message.tool_calls),
        }
        if choice.finish_reason:
            result["finish_reason"] = choice.finish_reason

        return result

    except Exception as e:
        logger.error("LLM completion failed: %s", str(e)[:500])
        return {"content": f"调用 LLM 时出错: {str(e)}", "tool_calls": []}
