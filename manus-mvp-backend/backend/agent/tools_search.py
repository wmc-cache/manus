"""
File Search Tools Module - find_files (glob) and grep_files (regex search).

Inspired by Manus 1.6 Max's `match` tool with `glob` and `grep` actions.
These tools enable the agent to efficiently locate files and search content
without needing to manually traverse directories.
"""
import asyncio
import fnmatch
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from sandbox.filesystem import get_workspace_root


def _get_workspace(conversation_id: Optional[str] = None) -> str:
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


# Directories to always skip
SKIP_DIRS = {
    "__pycache__", "node_modules", ".git", ".venv", "venv",
    ".mypy_cache", ".pytest_cache", ".tox", "context_memory",
    ".next", "dist", "build", ".cache",
}

MAX_GLOB_PATTERN_EXPANSIONS = 32


def _expand_brace_glob(pattern: str) -> List[str]:
    """
    Expand single-level brace glob patterns like:
    - **/*.{jpg,jpeg,png}
    - file.{py,ts}
    """
    if not isinstance(pattern, str):
        return [str(pattern)]

    left = pattern.find("{")
    right = pattern.find("}", left + 1)
    if left < 0 or right < 0 or right <= left + 1:
        return [pattern]

    inside = pattern[left + 1:right]
    options = [item.strip() for item in inside.split(",") if item.strip()]
    if not options:
        return [pattern]

    # Avoid generating too many expanded patterns.
    options = options[:MAX_GLOB_PATTERN_EXPANSIONS]
    prefix = pattern[:left]
    suffix = pattern[right + 1:]
    return [f"{prefix}{opt}{suffix}" for opt in options]

# Binary file extensions to skip in grep
BINARY_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".svg",
    ".mp3", ".mp4", ".wav", ".avi", ".mov",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".zip", ".tar", ".gz", ".bz2", ".7z", ".rar",
    ".exe", ".dll", ".so", ".dylib", ".o", ".a",
    ".pyc", ".pyo", ".whl", ".egg",
    ".woff", ".woff2", ".ttf", ".eot",
    ".sqlite", ".db",
}


async def find_files(
    pattern: str,
    path: str = ".",
    max_results: int = 50,
    conversation_id: Optional[str] = None,
) -> str:
    """
    Find files matching a glob pattern within the workspace.
    Similar to Manus 1.6 Max's `match` tool with `glob` action.

    Args:
        pattern: Glob pattern to match (e.g., "**/*.py", "*.md", "src/**/*.ts")
        path: Starting directory (relative to workspace, default ".")
        max_results: Maximum number of results to return
    """
    workspace = _get_workspace(conversation_id)
    try:
        resolved_path = _resolve_workspace_path(path, workspace)
    except ValueError as e:
        return f"查找文件出错: {str(e)}"

    if not os.path.isdir(resolved_path):
        return f"目录不存在: {path}"

    try:
        matches = []
        seen_paths = set()
        search_root = Path(resolved_path)
        expanded_patterns = _expand_brace_glob(pattern)
        if len(expanded_patterns) > MAX_GLOB_PATTERN_EXPANSIONS:
            expanded_patterns = expanded_patterns[:MAX_GLOB_PATTERN_EXPANSIONS]

        for expanded in expanded_patterns:
            for file_path in search_root.rglob(expanded):
                # Skip directories in skip list
                parts = file_path.relative_to(search_root).parts
                if any(part in SKIP_DIRS for part in parts):
                    continue

                rel_path = _to_workspace_relpath(str(file_path), workspace)
                if rel_path in seen_paths:
                    continue
                seen_paths.add(rel_path)

                is_dir = file_path.is_dir()

                if is_dir:
                    matches.append(f"📁 {rel_path}/")
                else:
                    try:
                        size = file_path.stat().st_size
                        size_str = _format_size(size)
                        matches.append(f"📄 {rel_path} ({size_str})")
                    except OSError:
                        matches.append(f"📄 {rel_path}")

                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        if not matches:
            return f"未找到匹配 `{pattern}` 的文件（搜索目录: {path}）"

        result = f"匹配 `{pattern}` 的文件（共 {len(matches)} 个）:\n\n"
        result += "\n".join(matches)

        if len(matches) >= max_results:
            result += f"\n\n... 结果已截断（最多显示 {max_results} 个）"

        return result

    except Exception as e:
        return f"查找文件出错: {str(e)}"


async def grep_files(
    regex: str,
    scope: str = "**/*",
    path: str = ".",
    leading: int = 0,
    trailing: int = 0,
    max_results: int = 30,
    max_file_size: int = 1024 * 1024,  # 1MB
    conversation_id: Optional[str] = None,
) -> str:
    """
    Search file contents using regex pattern matching.
    Similar to Manus 1.6 Max's `match` tool with `grep` action.

    Args:
        regex: Regular expression pattern to search for
        scope: Glob pattern to restrict which files to search (e.g., "**/*.py")
        path: Starting directory (relative to workspace, default ".")
        leading: Number of context lines before each match
        trailing: Number of context lines after each match
        max_results: Maximum number of matches to return
        max_file_size: Skip files larger than this size (bytes)
    """
    workspace = _get_workspace(conversation_id)
    try:
        resolved_path = _resolve_workspace_path(path, workspace)
    except ValueError as e:
        return f"搜索文件内容出错: {str(e)}"

    if not os.path.isdir(resolved_path):
        return f"目录不存在: {path}"

    try:
        compiled_regex = re.compile(regex)
    except re.error as e:
        return f"正则表达式语法错误: {str(e)}"

    try:
        search_root = Path(resolved_path)
        total_matches = 0
        file_matches = []

        # Collect files matching scope pattern
        target_files = []
        for file_path in search_root.rglob(scope):
            if not file_path.is_file():
                continue

            # Skip directories in skip list
            parts = file_path.relative_to(search_root).parts
            if any(part in SKIP_DIRS for part in parts):
                continue

            # Skip binary files
            if file_path.suffix.lower() in BINARY_EXTENSIONS:
                continue

            # Skip large files
            try:
                if file_path.stat().st_size > max_file_size:
                    continue
            except OSError:
                continue

            target_files.append(file_path)

        # Sort by modification time (newest first)
        target_files.sort(key=lambda f: f.stat().st_mtime, reverse=True)

        for file_path in target_files:
            if total_matches >= max_results:
                break

            try:
                with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                    lines = f.readlines()
            except (OSError, UnicodeDecodeError):
                continue

            file_hits = []
            for line_num, line in enumerate(lines, 1):
                if compiled_regex.search(line):
                    # Collect context lines
                    context_lines = []

                    # Leading context
                    for ctx_num in range(max(1, line_num - leading), line_num):
                        ctx_line = lines[ctx_num - 1].rstrip('\n')
                        context_lines.append(f"  {ctx_num:4d} | {ctx_line}")

                    # Match line (highlighted)
                    match_line = line.rstrip('\n')
                    context_lines.append(f"► {line_num:4d} | {match_line}")

                    # Trailing context
                    for ctx_num in range(line_num + 1, min(len(lines) + 1, line_num + trailing + 1)):
                        ctx_line = lines[ctx_num - 1].rstrip('\n')
                        context_lines.append(f"  {ctx_num:4d} | {ctx_line}")

                    file_hits.append("\n".join(context_lines))
                    total_matches += 1

                    if total_matches >= max_results:
                        break

            if file_hits:
                rel_path = _to_workspace_relpath(str(file_path), workspace)
                file_matches.append({
                    "path": rel_path,
                    "hits": file_hits,
                })

        if not file_matches:
            return f"未找到匹配 `{regex}` 的内容（搜索范围: {scope}，目录: {path}）"

        result = f"搜索 `{regex}` 的结果（共 {total_matches} 处匹配，{len(file_matches)} 个文件）:\n\n"

        for fm in file_matches:
            result += f"── {fm['path']} ──\n"
            for hit in fm["hits"]:
                result += hit + "\n"
            result += "\n"

        if total_matches >= max_results:
            result += f"... 结果已截断（最多显示 {max_results} 处匹配）"

        return result

    except Exception as e:
        return f"搜索文件内容出错: {str(e)}"


def _format_size(size: int) -> str:
    """Format file size in human-readable format."""
    if size < 1024:
        return f"{size}B"
    elif size < 1024 * 1024:
        return f"{size / 1024:.1f}KB"
    elif size < 1024 * 1024 * 1024:
        return f"{size / (1024 * 1024):.1f}MB"
    else:
        return f"{size / (1024 * 1024 * 1024):.1f}GB"


# ============ Tool Definitions for LLM ============

SEARCH_TOOL_DEFINITIONS = [
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
            "description": "使用正则表达式搜索文件内容。支持上下文行显示。结果按文件修改时间倒序排列。",
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


# ============ Search Tool Registry ============

SEARCH_TOOL_REGISTRY = {
    "find_files": {
        "func": find_files,
        "extract_args": lambda args: {
            "pattern": args.get("pattern"),
            "path": args.get("path", "."),
            "max_results": int(args.get("max_results", 50)),
        },
        "required_keys": ["pattern"],
        "non_empty_keys": ["pattern"],
        "string_keys": ["pattern", "path"],
        "usage_hint": '示例: {"pattern": "**/*.py"} 或 {"pattern": "*.md", "path": "docs"}',
    },
    "grep_files": {
        "func": grep_files,
        "extract_args": lambda args: {
            "regex": args.get("regex"),
            "scope": args.get("scope", "**/*"),
            "path": args.get("path", "."),
            "leading": int(args.get("leading", 0)),
            "trailing": int(args.get("trailing", 0)),
        },
        "required_keys": ["regex"],
        "non_empty_keys": ["regex"],
        "string_keys": ["regex", "scope", "path"],
        "usage_hint": '示例: {"regex": "def main", "scope": "**/*.py", "leading": 2, "trailing": 3}',
    },
}
