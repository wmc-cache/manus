"""DeepSeek API 封装 - 兼容 OpenAI SDK"""
import os
import json
from typing import AsyncGenerator, List, Dict, Any, Optional
from openai import AsyncOpenAI

# DeepSeek API 配置
DEEPSEEK_API_KEY = os.environ.get("DEEPSEEK_API_KEY", "")
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEEPSEEK_MODEL = "deepseek-chat"

client = AsyncOpenAI(
    api_key=DEEPSEEK_API_KEY,
    base_url=DEEPSEEK_BASE_URL,
)


# Agent 系统提示词
SYSTEM_PROMPT = """你是 Manus，一个强大的 AI Agent 助手。你在一台虚拟计算机上工作，用户可以实时看到你的操作过程。

你拥有以下工具能力：
1. **web_search** - 搜索互联网获取最新信息
2. **shell_exec** - 在终端中执行 shell 命令（用户可以在终端窗口看到）
3. **execute_code** - 执行 Python 代码（代码会显示在编辑器窗口中）
4. **browser_navigate** - 在浏览器中打开网页（用户可以在浏览器窗口看到截图）
5. **browser_get_content** - 获取当前浏览器页面的文本内容
6. **read_file** - 读取文件内容（文件会在编辑器窗口中显示）
7. **write_file** - 创建或写入文件（文件会在编辑器窗口中显示）

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


async def chat_completion_stream(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
) -> AsyncGenerator[Dict[str, Any], None]:
    """流式调用 DeepSeek API"""
    try:
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "stream": True,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        if use_tools:
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)

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
                    try:
                        args = json.loads(tc["arguments"])
                    except json.JSONDecodeError:
                        args = {}
                    yield {"type": "tool_call", "data": {"id": tc["id"], "name": tc["name"], "arguments": args}}
            elif finish_reason == "stop":
                yield {"type": "done", "data": {"content": current_content, "tool_calls": list(current_tool_calls.values())}}

    except Exception as e:
        yield {"type": "error", "data": str(e)}


async def chat_completion(
    messages: List[Dict[str, Any]],
    use_tools: bool = True,
) -> Dict[str, Any]:
    """非流式调用 DeepSeek API"""
    try:
        kwargs = {
            "model": DEEPSEEK_MODEL,
            "messages": [{"role": "system", "content": SYSTEM_PROMPT}] + messages,
            "temperature": 0.7,
            "max_tokens": 4096,
        }
        if use_tools:
            kwargs["tools"] = TOOLS
            kwargs["tool_choice"] = "auto"

        response = await client.chat.completions.create(**kwargs)
        choice = response.choices[0]

        result = {"content": choice.message.content or "", "tool_calls": []}

        if choice.message.tool_calls:
            for tc in choice.message.tool_calls:
                try:
                    args = json.loads(tc.function.arguments)
                except json.JSONDecodeError:
                    args = {}
                result["tool_calls"].append({"id": tc.id, "name": tc.function.name, "arguments": args})

        return result

    except Exception as e:
        return {"content": f"调用 LLM 时出错: {str(e)}", "tool_calls": []}
