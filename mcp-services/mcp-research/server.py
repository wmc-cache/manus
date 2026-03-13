"""
mcp-research: 搜索与研究 MCP 服务
提供网页搜索（Tavily）、并发批量研究（wide_research）、
子 Agent 并发任务（spawn_sub_agents）和数据分析能力
"""

import asyncio
import json
import logging
import os
import sys
import traceback
from typing import Any, Dict, List, Optional

import httpx
import uvicorn

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-shared"))
from mcp_base import MCPService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
WIDE_RESEARCH_MAX_ITEMS = int(os.environ.get("MANUS_WIDE_RESEARCH_MAX_ITEMS", "20"))
WIDE_RESEARCH_MAX_CONCURRENCY = int(os.environ.get("MANUS_WIDE_RESEARCH_CONCURRENCY", "5"))

# MCP Execution 服务地址（用于 spawn_sub_agents 调用代码执行）
MCP_EXECUTION_URL = os.environ.get("MCP_EXECUTION_URL", "http://localhost:8102")

# Agent 后端地址（用于 spawn_sub_agents 调用 LLM）
AGENT_BACKEND_URL = os.environ.get("AGENT_BACKEND_URL", "http://localhost:8000")
AGENT_API_TOKEN   = os.environ.get("MANUS_API_TOKEN", "")


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def web_search(query: str, conversation_id: Optional[str] = None) -> str:
    """使用 Tavily API 进行网页搜索"""
    if not TAVILY_API_KEY:
        return "未配置 TAVILY_API_KEY，无法执行网页搜索。"
    if not query or not query.strip():
        return "搜索关键词不能为空。"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                "https://api.tavily.com/search",
                json={
                    "api_key": TAVILY_API_KEY,
                    "query": query.strip(),
                    "search_depth": "basic",
                    "include_answer": True,
                    "max_results": 5,
                },
            )
            response.raise_for_status()
            data = response.json()

        results = []
        if data.get("answer"):
            results.append(f"**摘要**: {data['answer']}\n")

        for i, item in enumerate(data.get("results", [])[:5], 1):
            title   = item.get("title", "无标题")
            url     = item.get("url", "")
            content = item.get("content", "")
            if len(content) > 500:
                content = content[:500] + "..."
            results.append(f"{i}. **{title}**\n   {url}\n   {content}")

        return "\n\n".join(results) if results else "未找到相关搜索结果。"

    except httpx.HTTPStatusError as e:
        return f"搜索 API 请求失败（HTTP {e.response.status_code}）: {e}"
    except Exception as e:
        return f"搜索出错: {e}"


async def wide_research(
    query_template: str,
    items: List[str],
    conversation_id: Optional[str] = None,
) -> str:
    """
    对多个条目并发执行网页搜索，汇总结果。
    query_template 中的 {item} 会被替换为每个条目。
    """
    if not items:
        return "items 列表不能为空。"

    items = items[:WIDE_RESEARCH_MAX_ITEMS]
    semaphore = asyncio.Semaphore(WIDE_RESEARCH_MAX_CONCURRENCY)

    async def search_one(item: str) -> Dict[str, str]:
        query = query_template.replace("{item}", item)
        async with semaphore:
            result = await web_search(query, conversation_id)
            return {"item": item, "query": query, "result": result}

    tasks = [search_one(item) for item in items]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    lines = [f"# 批量研究结果（共 {len(items)} 项）\n"]
    for r in results:
        if isinstance(r, Exception):
            lines.append(f"## 错误\n{r}\n")
        else:
            lines.append(f"## {r['item']}\n**查询**: {r['query']}\n\n{r['result']}\n")

    return "\n---\n".join(lines)


async def spawn_sub_agents(
    task_template: str,
    items: List[str],
    reduce_goal: str = "",
    max_concurrency: Optional[int] = None,
    max_items: Optional[int] = None,
    conversation_id: Optional[str] = None,
) -> str:
    """
    对多个条目并发执行独立的搜索研究子任务，并汇总结果。
    task_template 中的 {item} 会被替换为每个条目。
    注意：此版本使用 web_search 实现子任务，完整版本应调用 Agent 后端。
    """
    if not items:
        return "items 列表不能为空。"

    _max_items = max_items or WIDE_RESEARCH_MAX_ITEMS
    _max_concurrency = max_concurrency or WIDE_RESEARCH_MAX_CONCURRENCY
    items = items[:_max_items]

    semaphore = asyncio.Semaphore(_max_concurrency)

    async def run_one(item: str) -> Dict[str, Any]:
        query = task_template.replace("{item}", item)
        async with semaphore:
            try:
                result = await web_search(query, conversation_id)
                return {"item": item, "success": True, "result": result}
            except Exception as e:
                return {"item": item, "success": False, "result": str(e)}

    tasks = [run_one(item) for item in items]
    results = await asyncio.gather(*tasks)

    lines = [f"# 子任务研究结果（共 {len(items)} 项）\n"]
    for r in results:
        status = "✓" if r["success"] else "✗"
        lines.append(f"## {status} {r['item']}\n{r['result']}\n")

    if reduce_goal:
        lines.append(f"\n---\n**汇总目标**: {reduce_goal}\n（请根据以上各条目的研究结果，完成上述汇总目标。）")

    return "\n---\n".join(lines)


async def data_analysis(
    data: str,
    task: str,
    conversation_id: Optional[str] = None,
) -> str:
    """
    对提供的数据执行分析任务。
    data 为 JSON 格式的数据，task 为分析目标描述。
    """
    try:
        parsed = json.loads(data)
    except json.JSONDecodeError:
        # 如果不是 JSON，当作文本数据处理
        parsed = data

    summary = []
    summary.append(f"**分析任务**: {task}")

    if isinstance(parsed, list):
        summary.append(f"**数据类型**: 列表，共 {len(parsed)} 条记录")
        if parsed and isinstance(parsed[0], dict):
            keys = list(parsed[0].keys())
            summary.append(f"**字段**: {', '.join(keys)}")
            summary.append(f"**前3条样本**:\n```json\n{json.dumps(parsed[:3], ensure_ascii=False, indent=2)}\n```")
    elif isinstance(parsed, dict):
        summary.append(f"**数据类型**: 对象，共 {len(parsed)} 个字段")
        summary.append(f"**字段**: {', '.join(list(parsed.keys())[:20])}")
    else:
        text = str(parsed)
        summary.append(f"**数据类型**: 文本，{len(text)} 字符")
        summary.append(f"**内容预览**: {text[:500]}")

    summary.append("\n（注意：如需执行复杂的数据分析，请使用 execute_code 工具编写 Python 代码进行处理。）")
    return "\n".join(summary)


# ---------------------------------------------------------------------------
# 服务构建
# ---------------------------------------------------------------------------

service = MCPService(
    name="mcp-research",
    version="1.0.0",
    description="Manus 搜索与研究服务，提供网页搜索、批量并发研究和数据分析能力",
)

service.register_tool(
    name="web_search",
    description="使用 Tavily API 搜索互联网，获取最新信息",
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "搜索关键词或问题"},
        },
        "required": ["query"],
    },
    func=web_search,
)

service.register_tool(
    name="wide_research",
    description="对多个条目并发执行网页搜索并汇总结果，适合批量信息收集",
    parameters={
        "type": "object",
        "properties": {
            "query_template": {
                "type": "string",
                "description": "搜索查询模板，使用 {item} 作为占位符，例如 '{item} 公司 2026 最新动态'",
            },
            "items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "要研究的条目列表，例如 ['OpenAI', 'Anthropic', 'Google DeepMind']",
            },
        },
        "required": ["query_template", "items"],
    },
    func=wide_research,
)

service.register_tool(
    name="spawn_sub_agents",
    description="对多个条目并发执行独立的研究子任务，并可指定汇总目标",
    parameters={
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
    func=spawn_sub_agents,
)

service.register_tool(
    name="data_analysis",
    description="对提供的 JSON 数据或文本数据进行结构化分析",
    parameters={
        "type": "object",
        "properties": {
            "data": {"type": "string", "description": "要分析的数据（JSON 字符串或文本）"},
            "task": {"type": "string", "description": "分析目标描述"},
        },
        "required": ["data", "task"],
    },
    func=data_analysis,
)

app = service.app

if __name__ == "__main__":
    port = int(os.environ.get("MCP_RESEARCH_PORT", "8104"))
    uvicorn.run(app, host="0.0.0.0", port=port)
