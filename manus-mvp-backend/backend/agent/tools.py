"""Agent 工具系统 - 定义和执行各种工具，联动计算机窗口（支持会话隔离）"""
import json
import os
import sys
import re
import asyncio
import traceback
from contextvars import ContextVar
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional, List
from urllib.parse import unquote, urlparse, parse_qs
import uuid

from llm.deepseek import chat_completion
from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.browser import browser_service
from sandbox.filesystem import notify_file_change, get_workspace_root
from sandbox.port_expose import port_expose_manager

# Import extended tools
try:
    from agent.tools_extended import (
        browser_click as _ext_browser_click,
        browser_input as _ext_browser_input,
        browser_scroll as _ext_browser_scroll,
        edit_file as _ext_edit_file,
        append_file as _ext_append_file,
        list_files as _ext_list_files,
        data_analysis as _ext_data_analysis,
        EXTENDED_TOOL_REGISTRY,
    )
    _EXTENDED_TOOLS_AVAILABLE = True
except ImportError:
    _EXTENDED_TOOLS_AVAILABLE = False
    EXTENDED_TOOL_REGISTRY = {}

# Import search tools (find_files, grep_files)
try:
    from agent.tools_search import (
        find_files as _search_find_files,
        grep_files as _search_grep_files,
        SEARCH_TOOL_REGISTRY,
    )
    _SEARCH_TOOLS_AVAILABLE = True
except ImportError:
    _SEARCH_TOOLS_AVAILABLE = False
    SEARCH_TOOL_REGISTRY = {}

# 当前执行上下文中的 conversation_id（由 execute_tool 设置，支持并发隔离）
_current_conversation_id: ContextVar[Optional[str]] = ContextVar(
    "current_conversation_id",
    default=None,
)


def _get_current_conversation_id() -> Optional[str]:
    return _current_conversation_id.get()


def _get_workspace(conversation_id: Optional[str] = None) -> str:
    """获取当前会话的 workspace 目录"""
    cid = conversation_id or _get_current_conversation_id()
    return get_workspace_root(cid)


def _publish_event(event_type: str, data: dict, window_id: Optional[str] = None):
    """创建带 conversation_id 的事件"""
    return SandboxEvent(
        event_type, data,
        window_id=window_id,
        conversation_id=_get_current_conversation_id(),
    )


def _resolve_workspace_path(path: str, workspace: str) -> str:
    """将相对路径安全解析到工作目录下，阻止绝对路径与目录穿越。"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不能为空")

    raw = path.strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("path 必须是相对路径，禁止使用绝对路径。")

    workspace_path = Path(workspace).resolve()
    resolved = (workspace_path / candidate).resolve()
    if resolved != workspace_path and workspace_path not in resolved.parents:
        raise ValueError("path 超出工作目录，禁止使用 .. 访问上级目录。")

    return str(resolved)


def _to_workspace_relpath(path: str, workspace: str) -> str:
    """将绝对路径转换为 workspace 相对路径，兼容 /tmp 与 /private/tmp 真实路径差异。"""
    workspace_path = Path(workspace).resolve()
    target_path = Path(path).resolve()
    return os.path.relpath(str(target_path), str(workspace_path))


# ============ 工具：网页搜索（Tavily API）============
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


def _resolve_tavily_api_key() -> str:
    # 优先显式环境变量
    env_key = os.environ.get("TAVILY_API_KEY", "").strip()
    if env_key:
        return env_key

    # 其次读取后端 .env
    backend_env = Path(__file__).resolve().parents[1] / ".env"
    file_key = _read_env_key_from_file(backend_env, "TAVILY_API_KEY")
    if file_key:
        return file_key

    # 最后兼容前端 .env（与 DEEPSEEK 读取策略一致）
    frontend_env = Path(__file__).resolve().parents[3] / "manus-frontend" / ".env"
    return _read_env_key_from_file(frontend_env, "TAVILY_API_KEY")


TAVILY_API_KEY = _resolve_tavily_api_key()
SAFE_ENV_KEYS = {
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TERM",
    "TZ",
    "SHELL",
    "USER",
    "LOGNAME",
    "TMPDIR",
    "TMP",
    "TEMP",
    "http_proxy",
    "https_proxy",
    "no_proxy",
    "HTTP_PROXY",
    "HTTPS_PROXY",
    "NO_PROXY",
}


def _read_positive_int_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
        return value if value > 0 else default
    except ValueError:
        return default


WIDE_RESEARCH_MAX_ITEMS = _read_positive_int_env("MANUS_WIDE_RESEARCH_MAX_ITEMS", 20)
WIDE_RESEARCH_MAX_CONCURRENCY = _read_positive_int_env("MANUS_WIDE_RESEARCH_CONCURRENCY", 5)
SUB_AGENT_MAX_ITEMS = _read_positive_int_env("MANUS_SUBAGENT_MAX_ITEMS", 20)
SUB_AGENT_MAX_CONCURRENCY = _read_positive_int_env("MANUS_SUBAGENT_CONCURRENCY", 5)
SUB_AGENT_MAX_ITERATIONS = _read_positive_int_env("MANUS_SUBAGENT_MAX_ITERATIONS", 4)
SUB_AGENT_MAX_ITEMS_HARD = _read_positive_int_env("MANUS_SUBAGENT_MAX_ITEMS_HARD", 100)
SUB_AGENT_MAX_CONCURRENCY_HARD = _read_positive_int_env("MANUS_SUBAGENT_CONCURRENCY_HARD", 20)
SUB_AGENT_MAX_ITERATIONS_HARD = _read_positive_int_env("MANUS_SUBAGENT_MAX_ITERATIONS_HARD", 12)
SUB_AGENT_MAX_TOOL_RESULT_CHARS = _read_positive_int_env("MANUS_SUBAGENT_MAX_TOOL_RESULT_CHARS", 4000)
SUB_AGENT_ALLOWED_TOOLS = ["web_search", "read_file", "write_file", "browser_navigate", "browser_get_content"]

SUB_AGENT_SYSTEM_PROMPT = (
    "你是并行深度研究任务中的子代理。\n"
    "你的职责是围绕当前 item 独立完成研究任务。\n\n"
    "工作流程：\n"
    "1. 使用 web_search 搜索相关信息\n"
    "2. 如果需要更详细的内容，使用 browser_navigate 访问搜索结果中的链接\n"
    "3. 使用 browser_get_content 获取页面内容\n"
    "4. 将研究结果用 write_file 保存到文件\n"
    "5. 给出结构化结论并停止调用工具\n\n"
    "要求：\n"
    "- 优先做事实收集与简洁总结\n"
    "- 当得到足够信息后，给出结构化结论并停止调用工具\n"
    "- 结论应包含：核心发现、关键数据、信息来源"
)


def _build_sandbox_env(workspace: str) -> Dict[str, str]:
    """构造工具子进程环境，默认不透传敏感变量。"""
    inherit_all = os.environ.get("MANUS_SANDBOX_INHERIT_ENV", "").strip().lower() in {
        "1", "true", "yes", "on"
    }
    if inherit_all:
        env = dict(os.environ)
    else:
        env = {k: v for k, v in os.environ.items() if k in SAFE_ENV_KEYS}

    env["HOME"] = workspace
    env["PWD"] = workspace
    env.setdefault("TERM", "xterm-256color")
    return env


def _decode_duckduckgo_href(href: str) -> str:
    if not href:
        return ""
    parsed = urlparse(href)
    if "duckduckgo.com" in parsed.netloc and parsed.path.startswith("/l/"):
        uddg = parse_qs(parsed.query).get("uddg", [])
        if uddg:
            return unquote(uddg[0])
    return href


def _duckduckgo_text_search(query: str, max_results: int = 5) -> List[Dict[str, Any]]:
    """通过 DuckDuckGo HTML 页面抓取搜索结果，避免第三方封装兼容噪声。"""
    import requests
    from lxml import html

    response = requests.get(
        "https://duckduckgo.com/html/",
        params={"q": query},
        timeout=15,
        headers={
            "User-Agent": "Mozilla/5.0 (compatible; ManusMVP/1.0)",
        },
    )
    response.raise_for_status()

    doc = html.fromstring(response.text)
    nodes = doc.xpath("//div[contains(@class, 'result')]")
    results: List[Dict[str, Any]] = []
    for node in nodes:
        title_parts = node.xpath(".//a[contains(@class,'result__a')]//text()")
        href_parts = node.xpath(".//a[contains(@class,'result__a')]/@href")
        snippet_parts = node.xpath(".//*[contains(@class,'result__snippet')]//text()")

        title = " ".join(part.strip() for part in title_parts if part.strip()).strip()
        href = href_parts[0].strip() if href_parts else ""
        body = " ".join(part.strip() for part in snippet_parts if part.strip()).strip()
        if not title and not href:
            continue

        results.append({
            "title": title or "无标题",
            "href": _decode_duckduckgo_href(href),
            "body": body,
        })

        if len(results) >= max_results:
            break

    return results

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

        loop = asyncio.get_event_loop()
        results = await loop.run_in_executor(
            None,
            lambda: _duckduckgo_text_search(query, max_results=5)
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


# ============ 工具：Wide Research（并行研究）============
def _slugify_name(text: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "_", text.strip())
    slug = slug.strip("._-")
    if not slug:
        return "item"
    return slug[:48]


def _preview_text(text: str, max_chars: int = 220) -> str:
    one_line = re.sub(r"\s+", " ", text).strip()
    if len(one_line) <= max_chars:
        return one_line
    return one_line[:max_chars] + "..."


def _clean_string_items(items: List[str]) -> List[str]:
    cleaned: List[str] = []
    for raw in items:
        if not isinstance(raw, str):
            continue
        value = raw.strip()
        if value:
            cleaned.append(value)
    return cleaned


def _resolve_positive_runtime_int(
    value: Optional[int],
    *,
    default: int,
    hard_max: int,
) -> int:
    if value is None:
        base = default
    else:
        try:
            base = int(value)
        except (TypeError, ValueError):
            base = default

    if base <= 0:
        base = default
    if base > hard_max:
        base = hard_max
    return base


def _normalize_sub_agent_rel_path(agent_rel_dir: str, raw_path: str) -> str:
    text = (raw_path or "").strip()
    if not text:
        return text
    if text == ".":
        return agent_rel_dir
    if text.startswith("/"):
        return text

    normalized = text.lstrip("./")
    prefix = f"{agent_rel_dir}/"
    if normalized == agent_rel_dir or normalized.startswith(prefix):
        return normalized
    return f"{agent_rel_dir}/{normalized}"


def _clip_for_sub_agent_context(text: str, max_chars: int = SUB_AGENT_MAX_TOOL_RESULT_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + f"\n... [子代理上下文截断 {len(text) - max_chars} 字符]"


def _serialize_sub_agent_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    序列化子代理消息轨迹，保留角色/文本/工具调用关键信息，
    并对长内容做裁剪，避免会话轨迹文件过大。
    """
    serialized: List[Dict[str, Any]] = []
    for msg in messages[-20:]:
        if not isinstance(msg, dict):
            continue
        role = str(msg.get("role", "") or "").strip()
        if not role:
            continue

        item: Dict[str, Any] = {"role": role}
        content = msg.get("content")
        if isinstance(content, str):
            item["content"] = _clip_for_sub_agent_context(content, max_chars=1600)

        tool_call_id = msg.get("tool_call_id")
        if isinstance(tool_call_id, str) and tool_call_id:
            item["tool_call_id"] = tool_call_id

        raw_tool_calls = msg.get("tool_calls")
        if isinstance(raw_tool_calls, list) and raw_tool_calls:
            cleaned_tool_calls = []
            for tc in raw_tool_calls[:3]:
                if not isinstance(tc, dict):
                    continue
                tc_id = str(tc.get("id", "") or "")
                tc_type = str(tc.get("type", "") or "function")
                fn = tc.get("function") if isinstance(tc.get("function"), dict) else {}
                fn_name = str(fn.get("name", "") or "")
                fn_args = fn.get("arguments", "")
                if not isinstance(fn_args, str):
                    fn_args = json.dumps(fn_args, ensure_ascii=False)
                cleaned_tool_calls.append({
                    "id": tc_id,
                    "type": tc_type,
                    "function": {
                        "name": fn_name,
                        "arguments": _clip_for_sub_agent_context(fn_args, max_chars=1200),
                    },
                })
            if cleaned_tool_calls:
                item["tool_calls"] = cleaned_tool_calls

        serialized.append(item)
    return serialized


async def _run_sub_agent_loop(
    *,
    item: str,
    prompt: str,
    agent_rel_dir: str,
    conversation_id: Optional[str],
    max_iterations: int,
) -> Dict[str, Any]:
    """
    轻量子代理循环：
    - 维护独立 messages
    - 允许少量工具调用
    - 每轮最多执行 1 个工具动作
    """
    messages: List[Dict[str, Any]] = [
        {"role": "system", "content": SUB_AGENT_SYSTEM_PROMPT},
        {
            "role": "user",
            "content": (
                f"item: {item}\n"
                f"任务: {prompt}\n"
                f"输出要求: 给出简明结论，并在必要时标注依据。"
            ),
        },
    ]

    iterations = 0
    tool_steps: List[Dict[str, Any]] = []
    final_answer = ""
    status = "completed"
    error_message = ""

    while iterations < max_iterations:
        iterations += 1
        llm_result = await chat_completion(
            messages=messages,
            use_tools=True,
            allowed_tool_names=SUB_AGENT_ALLOWED_TOOLS,
        )

        content = llm_result.get("content", "") or ""
        tool_calls = llm_result.get("tool_calls", []) or []

        if not isinstance(tool_calls, list):
            tool_calls = []

        if not tool_calls:
            final_answer = content.strip() or "子代理未生成明确结论。"
            messages.append({"role": "assistant", "content": content})
            break

        tc_data = tool_calls[0] if isinstance(tool_calls[0], dict) else {}
        tool_name = str(tc_data.get("name", "") or "").strip()
        tool_call_id = str(tc_data.get("id", "") or f"sub_agent_tc_{iterations}")
        raw_args = tc_data.get("arguments", {})
        args = raw_args if isinstance(raw_args, dict) else {}

        parse_error = tc_data.get("parse_error")
        if parse_error:
            status = "failed"
            error_message = f"子代理工具参数解析失败: {parse_error}"
            final_answer = error_message
            tool_steps.append({
                "step": iterations,
                "tool": tool_name or "(unknown)",
                "status": "failed",
                "error": error_message,
            })
            break

        if tool_name not in SUB_AGENT_ALLOWED_TOOLS:
            status = "failed"
            error_message = f"子代理请求了未允许工具: {tool_name}"
            final_answer = error_message
            tool_steps.append({
                "step": iterations,
                "tool": tool_name or "(unknown)",
                "status": "failed",
                "error": error_message,
            })
            break

        adjusted_args = dict(args)
        if tool_name in {"read_file", "write_file"}:
            path_val = adjusted_args.get("path")
            if isinstance(path_val, str) and path_val.strip():
                adjusted_args["path"] = _normalize_sub_agent_rel_path(agent_rel_dir, path_val)

        tool_result = await execute_tool(
            tool_name,
            adjusted_args,
            conversation_id=conversation_id,
        )

        tool_steps.append({
            "step": iterations,
            "tool": tool_name,
            "arguments": adjusted_args,
            "result_preview": _preview_text(tool_result, max_chars=260),
        })

        messages.append({
            "role": "assistant",
            "content": content,
            "tool_calls": [
                {
                    "id": tool_call_id,
                    "type": "function",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(adjusted_args, ensure_ascii=False),
                    },
                }
            ],
        })
        messages.append({
            "role": "tool",
            "tool_call_id": tool_call_id,
            "content": _clip_for_sub_agent_context(tool_result),
        })

    if not final_answer:
        # 到达迭代上限后，强制关闭工具并产出一次最终结论，避免只有“未完成”提示。
        final_llm = await chat_completion(
            messages=messages + [{
                "role": "user",
                "content": "请基于已有信息直接给出最终结论，不再调用任何工具。",
            }],
            use_tools=False,
        )
        fallback_answer = (final_llm.get("content", "") or "").strip()
        if fallback_answer:
            status = "completed_with_limit"
            final_answer = fallback_answer
        else:
            status = "max_iterations"
            final_answer = (
                f"子代理达到最大迭代轮数（{max_iterations}）仍未形成最终回答。"
            )

    return {
        "status": status,
        "final_answer": final_answer,
        "iterations": iterations,
        "tool_steps": tool_steps,
        "error": error_message,
        "messages": _serialize_sub_agent_messages(messages),
    }


async def _reduce_sub_agent_results(results: List[Dict[str, Any]], reduce_goal: str) -> str:
    compact = []
    for row in results:
        compact.append({
            "item": row.get("item"),
            "status": row.get("status"),
            "summary": row.get("summary"),
            "final_answer": row.get("final_answer"),
        })

    prompt = (
        "请基于以下子代理结果生成一段精炼的综合结论。"
        "输出结构：总体结论、关键差异、可执行建议。"
    )
    if reduce_goal.strip():
        prompt += f"\nReduce 目标: {reduce_goal.strip()}"

    llm_result = await chat_completion(
        messages=[
            {"role": "user", "content": prompt + "\n\n数据:\n" + json.dumps(compact, ensure_ascii=False)},
        ],
        use_tools=False,
    )
    content = (llm_result.get("content", "") or "").strip()
    return content


async def wide_research(query_template: str, items: List[str]) -> str:
    """
    并行处理一组同质研究任务。
    query_template 支持占位符 {item}，例如：\"{item} 公司最新融资进展\"。
    """
    cleaned_items = _clean_string_items(items)

    if not cleaned_items:
        return "wide_research 执行失败: items 不能为空，且必须是字符串数组。"

    truncated = False
    if len(cleaned_items) > WIDE_RESEARCH_MAX_ITEMS:
        cleaned_items = cleaned_items[:WIDE_RESEARCH_MAX_ITEMS]
        truncated = True

    workspace = _get_workspace()
    results_dir = _resolve_workspace_path("research", workspace)
    os.makedirs(results_dir, exist_ok=True)

    semaphore = asyncio.Semaphore(WIDE_RESEARCH_MAX_CONCURRENCY)
    conv_id = _get_current_conversation_id()

    async def run_one(index: int, item: str) -> Dict[str, str]:
        query = (
            query_template.replace("{item}", item)
            if "{item}" in query_template
            else f"{query_template.strip()} {item}".strip()
        )

        async with semaphore:
            content = await web_search(query)

        filename = f"{index:03d}_{_slugify_name(item)}.md"
        full_path = os.path.join(results_dir, filename)
        body = (
            f"# Research: {item}\n\n"
            f"- Query: {query}\n\n"
            "## Search Result\n\n"
            f"{content}\n"
        )
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(body)

        rel_path = _to_workspace_relpath(full_path, workspace)
        await notify_file_change(rel_path, "created", conv_id)

        return {
            "item": item,
            "query": query,
            "path": rel_path,
            "preview": _preview_text(content),
        }

    results = await asyncio.gather(
        *(run_one(i + 1, item) for i, item in enumerate(cleaned_items))
    )

    summary_lines = [
        "# Wide Research Summary",
        "",
        f"- Total items: {len(results)}",
        f"- Max concurrency: {WIDE_RESEARCH_MAX_CONCURRENCY}",
        "",
        "## Outputs",
    ]
    for row in results:
        summary_lines.append(f"- {row['item']}: `{row['path']}`")

    summary_lines.append("")
    summary_lines.append("## Quick Preview")
    for row in results:
        summary_lines.append(f"### {row['item']}")
        summary_lines.append(f"- Query: {row['query']}")
        summary_lines.append(f"- Preview: {row['preview']}")
        summary_lines.append("")

    summary_path = os.path.join(results_dir, "summary.md")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(summary_lines))

    summary_rel_path = _to_workspace_relpath(summary_path, workspace)
    await notify_file_change(summary_rel_path, "modified", conv_id)

    notice = (
        "已完成并行研究。"
        f"\n共处理 {len(results)} 个条目，汇总文件: {summary_rel_path}"
    )
    if truncated:
        notice += f"\n注意: 已按上限裁剪到前 {WIDE_RESEARCH_MAX_ITEMS} 个条目。"
    return notice


# ============ 工具：Spawn Sub Agents（最小多代理骨架）============
async def spawn_sub_agents(
    task_template: str,
    items: List[str],
    reduce_goal: str = "",
    max_concurrency: Optional[int] = None,
    max_items: Optional[int] = None,
    max_iterations: Optional[int] = None,
) -> str:
    """
    启动多个轻量子代理并行执行同质任务，并执行 reduce 汇总。
    每个子代理在独立目录写入 task/observation/result，最终产出汇总文件。
    """
    cleaned_items = _clean_string_items(items)
    if not cleaned_items:
        return "spawn_sub_agents 执行失败: items 不能为空，且必须是字符串数组。"

    effective_max_items = _resolve_positive_runtime_int(
        max_items,
        default=SUB_AGENT_MAX_ITEMS,
        hard_max=SUB_AGENT_MAX_ITEMS_HARD,
    )
    effective_max_concurrency = _resolve_positive_runtime_int(
        max_concurrency,
        default=SUB_AGENT_MAX_CONCURRENCY,
        hard_max=SUB_AGENT_MAX_CONCURRENCY_HARD,
    )
    effective_max_iterations = _resolve_positive_runtime_int(
        max_iterations,
        default=SUB_AGENT_MAX_ITERATIONS,
        hard_max=SUB_AGENT_MAX_ITERATIONS_HARD,
    )

    truncated = False
    if len(cleaned_items) > effective_max_items:
        cleaned_items = cleaned_items[:effective_max_items]
        truncated = True

    workspace = _get_workspace()
    conv_id = _get_current_conversation_id()
    parent_conversation_id = conv_id or "_default"
    base_dir = _resolve_workspace_path("multi_agent", workspace)
    agents_dir = os.path.join(base_dir, "agents")
    sessions_dir = os.path.join(base_dir, "sessions")
    os.makedirs(agents_dir, exist_ok=True)
    os.makedirs(sessions_dir, exist_ok=True)

    semaphore = asyncio.Semaphore(effective_max_concurrency)
    run_id = f"run_{datetime.now().strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"

    async def run_one(index: int, item: str) -> Dict[str, Any]:
        agent_id = f"{index:03d}_{_slugify_name(item)}"
        agent_dir = os.path.join(agents_dir, agent_id)
        os.makedirs(agent_dir, exist_ok=True)
        agent_rel_dir = _to_workspace_relpath(agent_dir, workspace)

        prompt = (
            task_template.replace("{item}", item)
            if "{item}" in task_template
            else f"{task_template.strip()} {item}".strip()
        )
        if not prompt:
            prompt = item

        task_md_path = os.path.join(agent_dir, "task.md")
        task_md = (
            f"# Sub Agent Task\n\n"
            f"- Agent ID: {agent_id}\n"
            f"- Item: {item}\n"
            f"- Created At: {datetime.now().isoformat()}\n\n"
            "## Prompt\n\n"
            f"{prompt}\n"
        )
        with open(task_md_path, "w", encoding="utf-8") as f:
            f.write(task_md)
        await notify_file_change(_to_workspace_relpath(task_md_path, workspace), "created", conv_id)

        try:
            async with semaphore:
                loop_result = await _run_sub_agent_loop(
                    item=item,
                    prompt=prompt,
                    agent_rel_dir=agent_rel_dir,
                    conversation_id=conv_id,
                    max_iterations=effective_max_iterations,
                )
        except Exception as exc:
            loop_result = {
                "status": "failed",
                "final_answer": f"子代理执行异常: {str(exc)}",
                "iterations": 0,
                "tool_steps": [],
                "error": str(exc),
            }

        status = str(loop_result.get("status", "failed"))
        final_answer = str(loop_result.get("final_answer", "") or "")
        error = str(loop_result.get("error", "") or "")
        tool_steps = loop_result.get("tool_steps", [])
        if not isinstance(tool_steps, list):
            tool_steps = []
        session_messages = loop_result.get("messages", [])
        if not isinstance(session_messages, list):
            session_messages = []
        iterations = int(loop_result.get("iterations", 0) or 0)

        observation_path = os.path.join(agent_dir, "observation.md")
        observation_body = [
            "# Sub Agent Observation",
            "",
            f"- Agent ID: {agent_id}",
            f"- Item: {item}",
            f"- Status: {status}",
            f"- Iterations: {iterations}",
            "",
            "## Final Answer",
            "",
            final_answer or "(empty)",
            "",
            "## Tool Steps",
            "",
        ]
        if tool_steps:
            for step in tool_steps:
                observation_body.append(f"- Step {step.get('step')}: {step.get('tool')} -> {step.get('result_preview', step.get('error', ''))}")
        else:
            observation_body.append("- (no tool call)")

        if error:
            observation_body.extend(["", "## Error", "", error])

        with open(observation_path, "w", encoding="utf-8") as f:
            f.write("\n".join(observation_body) + "\n")
        await notify_file_change(_to_workspace_relpath(observation_path, workspace), "created", conv_id)

        result = {
            "agent_id": agent_id,
            "item": item,
            "prompt": prompt,
            "status": status,
            "iterations": iterations,
            "summary": _preview_text(final_answer, max_chars=320),
            "final_answer": final_answer,
            "observation_path": _to_workspace_relpath(observation_path, workspace),
        }
        if tool_steps:
            result["tool_steps"] = tool_steps
        if error:
            result["error"] = error

        session_id = f"sa_{uuid.uuid4().hex[:12]}"
        session_record = {
            "id": session_id,
            "run_id": run_id,
            "parent_conversation_id": parent_conversation_id,
            "agent_id": agent_id,
            "item": item,
            "prompt": prompt,
            "workspace": agent_rel_dir,
            "status": status,
            "iterations": iterations,
            "final_answer": final_answer,
            "tool_steps": tool_steps,
            "messages": session_messages,
            "created_at": datetime.now().isoformat(),
        }
        if error:
            session_record["error"] = error

        session_path = os.path.join(sessions_dir, f"{session_id}.json")
        with open(session_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(session_record, ensure_ascii=False, indent=2))
        session_rel_path = _to_workspace_relpath(session_path, workspace)
        await notify_file_change(session_rel_path, "created", conv_id)
        result["session_id"] = session_id
        result["session_path"] = session_rel_path

        result_path = os.path.join(agent_dir, "result.json")
        with open(result_path, "w", encoding="utf-8") as f:
            f.write(json.dumps(result, ensure_ascii=False, indent=2))
        await notify_file_change(_to_workspace_relpath(result_path, workspace), "created", conv_id)
        return result

    results = await asyncio.gather(
        *(run_one(i + 1, item) for i, item in enumerate(cleaned_items))
    )

    reduce_lines = [
        "# Sub-Agent Reduce Summary",
        "",
        f"- Total agents: {len(results)}",
        f"- Max concurrency: {effective_max_concurrency}",
        f"- Max iterations per agent: {effective_max_iterations}",
        f"- Max items: {effective_max_items}",
    ]
    if reduce_goal.strip():
        reduce_lines.append(f"- Reduce goal: {reduce_goal.strip()}")
    reduce_lines.extend(["", "## Agent Results"])

    for row in results:
        reduce_lines.append(f"### {row['item']} ({row['status']})")
        reduce_lines.append(f"- Agent ID: {row['agent_id']}")
        reduce_lines.append(f"- Prompt: {row['prompt']}")
        reduce_lines.append(f"- Summary: {row['summary']}")
        reduce_lines.append(f"- Observation: `{row['observation_path']}`")
        if row.get("session_path"):
            reduce_lines.append(f"- Session: `{row['session_path']}`")
        if row.get("error"):
            reduce_lines.append(f"- Error: {row['error']}")
        reduce_lines.append("")

    reduce_llm_summary = ""
    try:
        reduce_llm_summary = (await _reduce_sub_agent_results(results, reduce_goal)).strip()
    except Exception:
        reduce_llm_summary = ""
    if reduce_llm_summary:
        reduce_lines.extend([
            "## LLM Reduce",
            "",
            reduce_llm_summary,
            "",
        ])

    reduce_summary_path = os.path.join(base_dir, "reduce_summary.md")
    with open(reduce_summary_path, "w", encoding="utf-8") as f:
        f.write("\n".join(reduce_lines))
    reduce_summary_rel = _to_workspace_relpath(reduce_summary_path, workspace)
    await notify_file_change(reduce_summary_rel, "modified", conv_id)

    reduce_json_path = os.path.join(base_dir, "reduce_results.json")
    with open(reduce_json_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(results, ensure_ascii=False, indent=2))
    reduce_json_rel = _to_workspace_relpath(reduce_json_path, workspace)
    await notify_file_change(reduce_json_rel, "modified", conv_id)

    index_payload = {
        "run_id": run_id,
        "parent_conversation_id": parent_conversation_id,
        "created_at": datetime.now().isoformat(),
        "task_template": task_template,
        "reduce_goal": reduce_goal,
        "limits": {
            "max_concurrency": effective_max_concurrency,
            "max_items": effective_max_items,
            "max_iterations": effective_max_iterations,
        },
        "sub_sessions": [
            {
                "session_id": row.get("session_id"),
                "session_path": row.get("session_path"),
                "agent_id": row.get("agent_id"),
                "item": row.get("item"),
                "status": row.get("status"),
                "observation_path": row.get("observation_path"),
            }
            for row in results
        ],
        "reduce_summary_path": reduce_summary_rel,
        "reduce_results_path": reduce_json_rel,
    }
    index_path = os.path.join(base_dir, "sub_agent_index.json")
    with open(index_path, "w", encoding="utf-8") as f:
        f.write(json.dumps(index_payload, ensure_ascii=False, indent=2))
    index_rel = _to_workspace_relpath(index_path, workspace)
    await notify_file_change(index_rel, "modified", conv_id)

    notice = (
        "已完成子代理并行执行与汇总。"
        f"\n子代理目录: {_to_workspace_relpath(agents_dir, workspace)}"
        f"\n会话索引: {index_rel}"
        f"\n汇总文件: {reduce_summary_rel}"
        f"\n结构化结果: {reduce_json_rel}"
        f"\n执行参数: concurrency={effective_max_concurrency}, items={effective_max_items}, iterations={effective_max_iterations}"
    )
    if truncated:
        notice += f"\n注意: 已按上限裁剪到前 {effective_max_items} 个条目。"
    return notice


# ============ 工具：Shell 命令执行（联动终端窗口）============
def _is_background_shell_command(command: str) -> bool:
    """判断是否为显式后台命令（以单个 & 结尾，例如 `python app.py &`）"""
    text = command.strip()
    return bool(re.search(r"(?<!&)&\s*$", text))


async def shell_exec(command: str) -> str:
    """在终端中执行 shell 命令，实时显示在计算机窗口"""
    workspace = _get_workspace()
    sandbox_env = _build_sandbox_env(workspace)
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
            cwd=workspace,
            env=sandbox_env,
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
    sandbox_env = _build_sandbox_env(workspace)
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
            cwd=workspace,
            env=sandbox_env,
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
        await notify_file_change("_temp_code.py", "created", _get_current_conversation_id())

        # 清理临时文件
        try:
            os.unlink(code_path)
        except OSError:
            pass

        return output

    except Exception as e:
        return f"代码执行出错: {str(e)}\n{traceback.format_exc()}"


# ============ 工具：暴露端口（生成可访问链接）============
async def expose_port(port: int, label: str = "") -> str:
    """暴露沙箱内的 Web 服务端口，生成可从实体机浏览器直接访问的链接"""
    from sandbox.port_expose import docker_exec_proxy
    conversation_id = _get_current_conversation_id() or "_default"
    internal_host = port_expose_manager.resolve_internal_host(conversation_id)

    # 在 macOS Docker Desktop bridge 模式下，容器 IP/名称对宿主机不可达。
    # 通过 docker exec TCP 隧道将本地端口转发到容器内部服务。
    target_port = 0
    if internal_host not in ("localhost", "127.0.0.1") and not internal_host.startswith("127."):
        try:
            from sandbox.docker_sandbox import _container_name
            container_name = _container_name(conversation_id)
            tunnel_key = f"{conversation_id}:{port}"
            local_port = await docker_exec_proxy.create_tunnel(container_name, port, tunnel_key)
            internal_host = "127.0.0.1"
            target_port = local_port
        except Exception as exc:
            logger.warning("创建 docker exec 隧道失败，使用直连: %s", exc)

    entry = port_expose_manager.expose(
        port=port,
        conversation_id=conversation_id,
        label=label or f"Port {port}",
        internal_host=internal_host,
        target_port=target_port,
    )

    # 端口可达性预检：避免在服务尚未启动时返回“成功链接”。
    # 最多重试约 2 秒，兼容刚启动的服务。
    actual_port = entry.target_port if entry.target_port else entry.port
    reachable = False
    for _ in range(8):
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(entry.internal_host, actual_port),
                timeout=0.4,
            )
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:
                pass
            reachable = True
            break
        except Exception:
            await asyncio.sleep(0.25)

    if not reachable:
        port_expose_manager.unexpose(port, conversation_id)
        return (
            f"端口暴露失败：无法连接到服务 {entry.internal_host}:{actual_port}。\n"
            "请先确认 Web 服务已成功启动并正在监听该端口，再调用 expose_port。\n"
            f"建议先执行：curl -I http://127.0.0.1:{port}/"
        )

    # 构建用户可访问的 URL
    # 这个 URL 通过后端反向代理路由转发到沙箱容器
    proxy_path = f"/proxy/{conversation_id}/{port}/"

    # 发布事件通知前端
    await event_bus.publish(SandboxEvent(
        "port_exposed",
        {
            "port": port,
            "label": entry.label,
            "proxy_path": proxy_path,
            "conversation_id": conversation_id,
        },
        window_id="browser",
        conversation_id=conversation_id,
    ))

    return (
        f"端口 {port} 已成功暴露！\n"
        f"用户可通过以下链接在浏览器中直接访问：\n"
        f"  相对路径: {proxy_path}\n"
        f"  完整地址: http://localhost:3000{proxy_path}"
    )


# ============ 工具：浏览器导航（联动浏览器窗口）============
async def browser_navigate(url: str) -> str:
    """在浏览器中打开指定 URL"""
    result = await browser_service.navigate(url, conversation_id=_get_current_conversation_id())
    if "error" in result:
        return f"浏览器导航失败: {result['error']}"
    return f"已打开网页: {result.get('title', '')} ({url})\n状态码: {result.get('status', 0)}"


# ============ 工具：浏览器截图 ============
async def browser_screenshot() -> str:
    """获取当前浏览器页面截图"""
    result = await browser_service.screenshot(conversation_id=_get_current_conversation_id())
    return f"已截取页面截图: {result.get('title', '')} ({result.get('url', '')})"


# ============ 工具：浏览器获取内容 ============
async def browser_get_content() -> str:
    """获取当前浏览器页面的文本内容"""
    content = await browser_service.get_content(conversation_id=_get_current_conversation_id())
    return content


# ============ 工具：读取文件（联动编辑器窗口）============
async def read_file(path: str) -> str:
    """读取文件内容"""
    workspace = _get_workspace()
    try:
        try:
            path = _resolve_workspace_path(path, workspace)
        except ValueError as e:
            return f"读取文件出错: {str(e)}"

        if not os.path.exists(path):
            return f"文件不存在: {path}"

        with open(path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        # 通知编辑器窗口
        rel_path = _to_workspace_relpath(path, workspace)
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

        try:
            path = _resolve_workspace_path(path, workspace)
        except ValueError as e:
            return f"写入文件出错: {str(e)}"

        if os.path.isdir(path):
            return f"写入文件出错: path 指向目录而不是文件: {path}"

        dir_path = os.path.dirname(path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        existed = os.path.exists(path)

        with open(path, 'w', encoding='utf-8') as f:
            f.write(content)

        # 通知编辑器窗口
        rel_path = _to_workspace_relpath(path, workspace)
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
        await notify_file_change(
            rel_path,
            "modified" if existed else "created",
            _get_current_conversation_id(),
        )

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
    "wide_research": {
        "func": wide_research,
        "extract_args": lambda args: {
            "query_template": args.get("query_template"),
            "items": args.get("items"),
        },
        "required_keys": ["query_template", "items"],
        "non_empty_keys": ["query_template"],
        "string_keys": ["query_template"],
        "list_string_keys": ["items"],
        "usage_hint": (
            '示例: {"query_template": "{item} 公司 2026 最新动态", '
            '"items": ["OpenAI", "Anthropic", "Google DeepMind"]}'
        ),
    },
    "spawn_sub_agents": {
        "func": spawn_sub_agents,
        "extract_args": lambda args: {
            "task_template": args.get("task_template"),
            "items": args.get("items"),
            "reduce_goal": args.get("reduce_goal", ""),
            "max_concurrency": args.get("max_concurrency"),
            "max_items": args.get("max_items"),
            "max_iterations": args.get("max_iterations"),
        },
        "required_keys": ["task_template", "items"],
        "non_empty_keys": ["task_template"],
        "string_keys": ["task_template", "reduce_goal"],
        "list_string_keys": ["items"],
        "int_keys": ["max_concurrency", "max_items", "max_iterations"],
        "usage_hint": (
            '示例: {"task_template": "调研 {item} 2026 最新产品与商业动态", '
            '"items": ["OpenAI", "Anthropic"], "reduce_goal": "对比商业化进展", '
            '"max_concurrency": 3, "max_items": 20, "max_iterations": 4}'
        ),
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
    "expose_port": {
        "func": expose_port,
        "extract_args": lambda args: {
            "port": int(args.get("port", 0)),
            "label": args.get("label", ""),
        },
        "required_keys": ["port"],
        "int_keys": ["port"],
        "string_keys": ["label"],
        "usage_hint": '示例: {"port": 8080, "label": "我的网站"}',
    },
}


# Merge extended tools into registry
if _EXTENDED_TOOLS_AVAILABLE:
    for _ext_name, _ext_config in EXTENDED_TOOL_REGISTRY.items():
        if _ext_name not in TOOL_REGISTRY:
            TOOL_REGISTRY[_ext_name] = _ext_config

# Merge search tools into registry
if _SEARCH_TOOLS_AVAILABLE:
    for _search_name, _search_config in SEARCH_TOOL_REGISTRY.items():
        if _search_name not in TOOL_REGISTRY:
            TOOL_REGISTRY[_search_name] = _search_config


async def execute_tool(name: str, arguments: Dict[str, Any], conversation_id: Optional[str] = None) -> str:
    """执行指定工具（带会话隔离）"""
    token = _current_conversation_id.set(conversation_id)

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
        list_string_keys = tool.get("list_string_keys", [])
        int_keys = tool.get("int_keys", [])
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

        wrong_list_keys = []
        for k in list_string_keys:
            v = kwargs.get(k)
            if not isinstance(v, list) or not v:
                wrong_list_keys.append(k)
                continue
            if any((not isinstance(item, str)) or (not item.strip()) for item in v):
                wrong_list_keys.append(k)
        if wrong_list_keys:
            hint = f" {usage_hint}" if usage_hint else ""
            raise ValueError(
                f"工具 `{name}` 参数格式错误(应为非空字符串数组): {', '.join(wrong_list_keys)}。{hint}".strip()
            )

        wrong_int_keys = []
        for k in int_keys:
            v = kwargs.get(k)
            if v is None:
                continue
            if not isinstance(v, int) or v <= 0:
                wrong_int_keys.append(k)
        if wrong_int_keys:
            hint = f" {usage_hint}" if usage_hint else ""
            raise ValueError(f"工具 `{name}` 参数类型错误(应为正整数): {', '.join(wrong_int_keys)}。{hint}".strip())

        result = await tool["func"](**kwargs)
        return result
    finally:
        _current_conversation_id.reset(token)
