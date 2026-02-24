"""
Extended Tools Module - Additional tools to enhance Agent capabilities.

New tools:
1. browser_click - Click elements on web pages
2. browser_input - Type text into form fields
3. browser_scroll - Scroll web pages
4. edit_file - Targeted file edits (find & replace) instead of full rewrite
5. list_files - List directory contents with tree view
6. data_analysis - Execute Python data analysis with automatic visualization
7. append_file - Append content to existing files
"""
import asyncio
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from sandbox.event_bus import event_bus, SandboxEvent
from sandbox.browser import browser_service
from sandbox.filesystem import notify_file_change, get_workspace_root


def _get_workspace_from_conv(conversation_id: Optional[str] = None) -> str:
    return get_workspace_root(conversation_id)


def _resolve_workspace_path(path: str, workspace: str) -> str:
    """Resolve relative path safely within workspace."""
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
    workspace_path = Path(workspace).resolve()
    target_path = Path(path).resolve()
    return os.path.relpath(str(target_path), str(workspace_path))


def _get_lang(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".html": "html", ".css": "css", ".json": "json",
        ".md": "markdown", ".sh": "shell", ".yaml": "yaml",
        ".yml": "yaml", ".sql": "sql", ".xml": "xml",
        ".jsx": "javascript", ".tsx": "typescript", ".vue": "vue",
        ".go": "go", ".rs": "rust", ".java": "java",
        ".c": "c", ".cpp": "cpp", ".h": "c", ".hpp": "cpp",
        ".rb": "ruby", ".php": "php", ".swift": "swift",
        ".kt": "kotlin", ".scala": "scala", ".r": "r",
        ".toml": "toml", ".ini": "ini", ".cfg": "ini",
        ".env": "shell", ".dockerfile": "dockerfile",
    }
    return lang_map.get(ext, "plaintext")


# ============ Tool: Browser Click ============
async def browser_click(
    x: float,
    y: float,
    viewport_width: float = 1280,
    viewport_height: float = 720,
    conversation_id: Optional[str] = None,
) -> str:
    """Click at specific coordinates on the browser page."""
    try:
        result = await browser_service.click_by_coordinates(
            x, y, viewport_width, viewport_height,
            conversation_id=conversation_id,
        )
        if result.get("success"):
            return f"已点击坐标 ({x}, {y})，页面可能已更新。"
        return f"点击失败: {result.get('error', '未知错误')}"
    except Exception as e:
        return f"浏览器点击出错: {str(e)}"


# ============ Tool: Browser Input ============
async def browser_input(
    text: str,
    submit: bool = False,
    conversation_id: Optional[str] = None,
) -> str:
    """Type text into the currently focused element on the browser page."""
    try:
        result = await browser_service.type_text(
            text, submit,
            conversation_id=conversation_id,
        )
        if result.get("success"):
            action = "并提交" if submit else ""
            return f"已输入文本{action}: {text[:100]}"
        return f"输入失败: {result.get('error', '未知错误')}"
    except Exception as e:
        return f"浏览器输入出错: {str(e)}"


# ============ Tool: Browser Scroll ============
async def browser_scroll(
    direction: str = "down",
    amount: float = 300,
    conversation_id: Optional[str] = None,
) -> str:
    """Scroll the browser page."""
    try:
        delta_y = amount if direction == "down" else -amount
        result = await browser_service.scroll(
            delta_y,
            conversation_id=conversation_id,
        )
        if result.get("success"):
            return f"已向{direction}滚动 {amount} 像素。"
        return f"滚动失败: {result.get('error', '未知错误')}"
    except Exception as e:
        return f"浏览器滚动出错: {str(e)}"


# ============ Tool: Edit File (targeted find & replace) ============
async def edit_file(
    path: str,
    edits: List[Dict[str, str]],
    conversation_id: Optional[str] = None,
) -> str:
    """
    Apply targeted edits to a file using find & replace.
    Each edit is a dict with 'find' and 'replace' keys.
    This is more efficient than rewriting the entire file.
    """
    workspace = _get_workspace_from_conv(conversation_id)
    try:
        resolved_path = _resolve_workspace_path(path, workspace)
    except ValueError as e:
        return f"编辑文件出错: {str(e)}"

    if not os.path.exists(resolved_path):
        return f"文件不存在: {path}"

    try:
        with open(resolved_path, 'r', encoding='utf-8', errors='replace') as f:
            content = f.read()

        original_content = content
        applied = 0
        failed = []

        for i, edit in enumerate(edits):
            find_text = edit.get("find", "")
            replace_text = edit.get("replace", "")

            if not find_text:
                failed.append(f"编辑 {i + 1}: find 不能为空")
                continue

            if find_text not in content:
                failed.append(f"编辑 {i + 1}: 未找到匹配文本 '{find_text[:60]}...'")
                continue

            # Replace only the first occurrence by default
            replace_all = edit.get("all", False)
            if replace_all:
                content = content.replace(find_text, replace_text)
            else:
                content = content.replace(find_text, replace_text, 1)
            applied += 1

        if applied == 0:
            return f"编辑文件失败: 所有编辑均未匹配。\n" + "\n".join(failed)

        with open(resolved_path, 'w', encoding='utf-8') as f:
            f.write(content)

        rel_path = _to_workspace_relpath(resolved_path, workspace)
        await notify_file_change(rel_path, "modified", conversation_id)

        # Notify editor
        await event_bus.publish(SandboxEvent(
            "file_opened",
            {
                "path": rel_path,
                "name": os.path.basename(resolved_path),
                "content": content[:10000],
                "language": _get_lang(resolved_path),
            },
            window_id="editor",
            conversation_id=conversation_id,
        ))

        result = f"文件已编辑: {path} (成功 {applied}/{len(edits)} 处修改)"
        if failed:
            result += "\n失败项:\n" + "\n".join(failed)
        return result

    except Exception as e:
        return f"编辑文件出错: {str(e)}"


# ============ Tool: Append File ============
async def append_file(
    path: str,
    content: str,
    conversation_id: Optional[str] = None,
) -> str:
    """Append content to an existing file."""
    workspace = _get_workspace_from_conv(conversation_id)
    try:
        resolved_path = _resolve_workspace_path(path, workspace)
    except ValueError as e:
        return f"追加文件出错: {str(e)}"

    try:
        dir_path = os.path.dirname(resolved_path)
        if dir_path:
            os.makedirs(dir_path, exist_ok=True)

        with open(resolved_path, 'a', encoding='utf-8') as f:
            f.write(content)

        rel_path = _to_workspace_relpath(resolved_path, workspace)
        await notify_file_change(rel_path, "modified", conversation_id)

        return f"内容已追加到文件: {path} (+{len(content)} 字符)"

    except Exception as e:
        return f"追加文件出错: {str(e)}"


# ============ Tool: List Files ============
async def list_files(
    path: str = ".",
    max_depth: int = 3,
    conversation_id: Optional[str] = None,
) -> str:
    """List directory contents in a tree-like format."""
    workspace = _get_workspace_from_conv(conversation_id)
    try:
        resolved_path = _resolve_workspace_path(path, workspace)
    except ValueError as e:
        return f"列出文件出错: {str(e)}"

    if not os.path.exists(resolved_path):
        return f"目录不存在: {path}"

    if not os.path.isdir(resolved_path):
        return f"路径不是目录: {path}"

    lines = []
    file_count = 0
    dir_count = 0

    def _walk(dir_path: str, prefix: str, depth: int):
        nonlocal file_count, dir_count
        if depth > max_depth:
            lines.append(f"{prefix}... (已达最大深度)")
            return

        try:
            entries = sorted(os.listdir(dir_path))
        except PermissionError:
            lines.append(f"{prefix}[权限不足]")
            return

        # Filter out hidden and common ignore patterns
        ignore_patterns = {
            "__pycache__", "node_modules", ".git", ".venv",
            "venv", ".mypy_cache", ".pytest_cache", ".tox",
            "context_memory", ".DS_Store",
        }
        entries = [e for e in entries if e not in ignore_patterns]

        dirs = []
        files = []
        for entry in entries:
            full = os.path.join(dir_path, entry)
            if os.path.isdir(full):
                dirs.append(entry)
            else:
                files.append(entry)

        all_entries = [(d, True) for d in dirs] + [(f, False) for f in files]

        for i, (entry, is_dir) in enumerate(all_entries):
            is_last = i == len(all_entries) - 1
            connector = "└── " if is_last else "├── "
            extension = "    " if is_last else "│   "

            if is_dir:
                dir_count += 1
                lines.append(f"{prefix}{connector}{entry}/")
                _walk(os.path.join(dir_path, entry), prefix + extension, depth + 1)
            else:
                file_count += 1
                size = os.path.getsize(os.path.join(dir_path, entry))
                size_str = _format_size(size)
                lines.append(f"{prefix}{connector}{entry} ({size_str})")

    rel_path = _to_workspace_relpath(resolved_path, workspace)
    lines.append(f"{rel_path}/")
    _walk(resolved_path, "", 1)
    lines.append(f"\n共 {dir_count} 个目录, {file_count} 个文件")

    return "\n".join(lines)


def _format_size(size: int) -> str:
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    else:
        return f"{size / (1024 * 1024):.1f}MB"


# ============ Tool: Data Analysis ============
async def data_analysis(
    code: str,
    description: str = "",
    conversation_id: Optional[str] = None,
) -> str:
    """
    Execute Python code for data analysis with automatic chart saving.
    Pre-imports pandas, numpy, matplotlib, seaborn for convenience.
    """
    workspace = _get_workspace_from_conv(conversation_id)

    # Build safe environment
    safe_env_keys = {
        "PATH", "LANG", "LC_ALL", "LC_CTYPE", "TERM", "TZ",
        "SHELL", "USER", "LOGNAME", "TMPDIR",
    }
    env = {k: v for k, v in os.environ.items() if k in safe_env_keys}
    env["HOME"] = workspace
    env["PWD"] = workspace
    env.setdefault("TERM", "xterm-256color")

    # Wrap code with auto-imports and chart saving
    wrapped_code = f'''
import os
import sys
import warnings
warnings.filterwarnings("ignore")

# Auto-imports for data analysis
try:
    import pandas as pd
    import numpy as np
except ImportError:
    pass

try:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.font_manager as fm
    # Try to use CJK font
    for font_name in ["SimHei", "WenQuanYi Micro Hei", "Noto Sans CJK SC", "DejaVu Sans"]:
        if any(font_name in f.name for f in fm.fontManager.ttflist):
            plt.rcParams["font.sans-serif"] = [font_name]
            break
    plt.rcParams["axes.unicode_minus"] = False
except ImportError:
    pass

try:
    import seaborn as sns
    sns.set_theme(style="whitegrid")
except ImportError:
    pass

# User code
{code}

# Auto-save any open matplotlib figures
try:
    import matplotlib.pyplot as plt
    figs = [plt.figure(i) for i in plt.get_fignums()]
    for idx, fig in enumerate(figs):
        chart_path = os.path.join("{workspace}", f"chart_{{idx + 1}}.png")
        fig.savefig(chart_path, dpi=150, bbox_inches="tight")
        print(f"[图表已保存] chart_{{idx + 1}}.png")
    plt.close("all")
except Exception:
    pass
'''

    code_path = os.path.join(workspace, "_data_analysis.py")
    with open(code_path, 'w', encoding='utf-8') as f:
        f.write(wrapped_code)

    # Notify editor
    await event_bus.publish(SandboxEvent(
        "file_opened",
        {
            "path": "_data_analysis.py",
            "name": "_data_analysis.py",
            "content": code,
            "language": "python",
        },
        window_id="editor",
        conversation_id=conversation_id,
    ))

    try:
        process = await asyncio.create_subprocess_exec(
            sys.executable, code_path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=workspace,
            env=env,
        )

        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate(), timeout=60  # Longer timeout for data analysis
            )
        except asyncio.TimeoutError:
            process.kill()
            await process.communicate()
            return "数据分析执行超时（60秒限制）"

        output = ""
        if stdout:
            output += stdout.decode('utf-8', errors='replace')
        if stderr:
            stderr_text = stderr.decode('utf-8', errors='replace')
            if stderr_text.strip():
                output += "\n[STDERR]\n" + stderr_text

        if not output.strip():
            output = "数据分析执行成功（无输出）"

        if len(output) > 5000:
            output = output[:5000] + "\n... [输出被截断]"

        # Notify file changes for any generated charts
        for f_name in os.listdir(workspace):
            if f_name.startswith("chart_") and f_name.endswith(".png"):
                await notify_file_change(f_name, "created", conversation_id)

        return output

    except Exception as e:
        return f"数据分析执行出错: {str(e)}\n{traceback.format_exc()}"
    finally:
        try:
            os.unlink(code_path)
        except OSError:
            pass


# ============ Extended Tool Definitions (OpenAI Function Calling format) ============

EXTENDED_TOOLS = [
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
                    },
                    "viewport_width": {
                        "type": "number",
                        "description": "视口宽度（默认 1280）",
                    },
                    "viewport_height": {
                        "type": "number",
                        "description": "视口高度（默认 720）",
                    },
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
                                    "description": "是否替换所有匹配（默认 false，只替换第一个）"
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
]


# ============ Extended Tool Registry ============

EXTENDED_TOOL_REGISTRY = {
    "browser_click": {
        "func": browser_click,
        "extract_args": lambda args: {
            "x": float(args.get("x", 0)),
            "y": float(args.get("y", 0)),
            "viewport_width": float(args.get("viewport_width", 1280)),
            "viewport_height": float(args.get("viewport_height", 720)),
        },
        "required_keys": ["x", "y"],
        "usage_hint": '示例: {"x": 640, "y": 360}',
    },
    "browser_input": {
        "func": browser_input,
        "extract_args": lambda args: {
            "text": args.get("text", ""),
            "submit": bool(args.get("submit", False)),
        },
        "required_keys": ["text"],
        "non_empty_keys": ["text"],
        "string_keys": ["text"],
        "usage_hint": '示例: {"text": "搜索关键词", "submit": true}',
    },
    "browser_scroll": {
        "func": browser_scroll,
        "extract_args": lambda args: {
            "direction": args.get("direction", "down"),
            "amount": float(args.get("amount", 300)),
        },
        "required_keys": ["direction"],
        "string_keys": ["direction"],
        "usage_hint": '示例: {"direction": "down", "amount": 500}',
    },
    "edit_file": {
        "func": edit_file,
        "extract_args": lambda args: {
            "path": args.get("path"),
            "edits": args.get("edits", []),
        },
        "required_keys": ["path", "edits"],
        "non_empty_keys": ["path"],
        "string_keys": ["path"],
        "usage_hint": '示例: {"path": "app.py", "edits": [{"find": "old_text", "replace": "new_text"}]}',
    },
    "append_file": {
        "func": append_file,
        "extract_args": lambda args: {
            "path": args.get("path"),
            "content": args.get("content", ""),
        },
        "required_keys": ["path", "content"],
        "non_empty_keys": ["path"],
        "string_keys": ["path", "content"],
        "usage_hint": '示例: {"path": "log.txt", "content": "新增内容\\n"}',
    },
    "list_files": {
        "func": list_files,
        "extract_args": lambda args: {
            "path": args.get("path", "."),
            "max_depth": int(args.get("max_depth", 3)),
        },
        "usage_hint": '示例: {"path": ".", "max_depth": 2}',
    },
    "data_analysis": {
        "func": data_analysis,
        "extract_args": lambda args: {
            "code": args.get("code"),
            "description": args.get("description", ""),
        },
        "required_keys": ["code"],
        "non_empty_keys": ["code"],
        "string_keys": ["code", "description"],
        "usage_hint": '示例: {"code": "import pandas as pd\\ndf = pd.read_csv(\\"data.csv\\")\\nprint(df.describe())"}',
    },
}
