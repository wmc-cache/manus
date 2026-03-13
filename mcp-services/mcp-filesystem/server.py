"""
mcp-filesystem: 文件系统操作 MCP 服务
提供工作区内的文件读写、编辑、搜索等能力
"""

import logging
import os
import re
import sys
from pathlib import Path
from typing import Optional

import uvicorn

# 将共享库加入路径
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp-shared"))
from mcp_base import MCPService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger(__name__)

WORKSPACE_BASE = os.environ.get("MANUS_WORKSPACE_BASE", "/tmp/manus_workspace")
CONVERSATION_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


# ---------------------------------------------------------------------------
# 工作区路径工具
# ---------------------------------------------------------------------------

def _get_workspace(conversation_id: Optional[str]) -> str:
    if conversation_id and CONVERSATION_ID_PATTERN.fullmatch(conversation_id):
        path = os.path.join(WORKSPACE_BASE, conversation_id)
    else:
        path = os.path.join(WORKSPACE_BASE, "_default")
    os.makedirs(path, exist_ok=True)
    return path


def _resolve_path(path: str, workspace: str) -> str:
    """安全解析路径，防止目录穿越"""
    if not isinstance(path, str) or not path.strip():
        raise ValueError("path 不能为空")
    raw = path.strip()
    candidate = Path(raw)
    if candidate.is_absolute():
        raise ValueError("path 必须是相对路径，禁止使用绝对路径")
    workspace_path = Path(workspace).resolve()
    resolved = (workspace_path / candidate).resolve()
    if resolved != workspace_path and workspace_path not in resolved.parents:
        raise ValueError("path 超出工作目录范围，禁止使用 .. 访问上级目录")
    return str(resolved)


# ---------------------------------------------------------------------------
# 工具实现
# ---------------------------------------------------------------------------

async def read_file(path: str, conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path, workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    if not os.path.exists(full_path):
        return f"文件不存在: {path}"
    if os.path.isdir(full_path):
        return f"路径是目录，非文件: {path}"
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        if len(content) > 50000:
            content = content[:50000] + "\n... [内容被截断，超过50000字符]"
        return content
    except Exception as e:
        return f"读取文件失败: {e}"


async def write_file(path: str, content: str, conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path, workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(full_path, workspace)
        return f"文件已写入: {rel}（{len(content)} 字符）"
    except Exception as e:
        return f"写入文件失败: {e}"


async def append_file(path: str, content: str, conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path, workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    try:
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, "a", encoding="utf-8") as f:
            f.write(content)
        rel = os.path.relpath(full_path, workspace)
        return f"内容已追加到: {rel}"
    except Exception as e:
        return f"追加文件失败: {e}"


async def edit_file(path: str, old_content: str, new_content: str, conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path, workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    if not os.path.exists(full_path):
        return f"文件不存在: {path}"
    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            current = f.read()
        if old_content not in current:
            return f"未找到要替换的内容（请检查 old_content 是否与文件内容完全匹配）"
        updated = current.replace(old_content, new_content, 1)
        with open(full_path, "w", encoding="utf-8") as f:
            f.write(updated)
        rel = os.path.relpath(full_path, workspace)
        return f"文件已编辑: {rel}"
    except Exception as e:
        return f"编辑文件失败: {e}"


async def list_files(path: str = ".", conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path if path else ".", workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    if not os.path.exists(full_path):
        return f"目录不存在: {path}"
    try:
        lines = []
        for root, dirs, files in os.walk(full_path):
            dirs[:] = sorted([d for d in dirs if not d.startswith(".")])
            rel_root = os.path.relpath(root, workspace)
            depth = rel_root.count(os.sep) if rel_root != "." else 0
            if depth > 4:
                continue
            indent = "  " * depth
            folder_name = os.path.basename(root) if root != full_path else (path or ".")
            lines.append(f"{indent}{folder_name}/")
            for fname in sorted(files):
                if not fname.startswith("."):
                    fpath = os.path.join(root, fname)
                    size = os.path.getsize(fpath)
                    lines.append(f"{indent}  {fname} ({size} bytes)")
        return "\n".join(lines) if lines else "（空目录）"
    except Exception as e:
        return f"列出文件失败: {e}"


async def find_files(pattern: str, path: str = ".", conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path if path else ".", workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    try:
        import fnmatch
        matches = []
        for root, dirs, files in os.walk(full_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                if fnmatch.fnmatch(fname, pattern):
                    fpath = os.path.join(root, fname)
                    rel = os.path.relpath(fpath, workspace)
                    matches.append(rel)
            if len(matches) >= 100:
                break
        if not matches:
            return f"未找到匹配 `{pattern}` 的文件"
        return "\n".join(matches[:100])
    except Exception as e:
        return f"查找文件失败: {e}"


async def grep_files(pattern: str, path: str = ".", conversation_id: Optional[str] = None) -> str:
    workspace = _get_workspace(conversation_id)
    try:
        full_path = _resolve_path(path if path else ".", workspace)
    except ValueError as e:
        return f"路径错误: {e}"
    try:
        compiled = re.compile(pattern)
    except re.error as e:
        return f"正则表达式错误: {e}"
    try:
        results = []
        for root, dirs, files in os.walk(full_path):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fname in files:
                fpath = os.path.join(root, fname)
                try:
                    with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                        for lineno, line in enumerate(f, 1):
                            if compiled.search(line):
                                rel = os.path.relpath(fpath, workspace)
                                results.append(f"{rel}:{lineno}: {line.rstrip()}")
                                if len(results) >= 200:
                                    break
                except Exception:
                    continue
                if len(results) >= 200:
                    break
            if len(results) >= 200:
                break
        if not results:
            return f"未找到匹配 `{pattern}` 的内容"
        return "\n".join(results)
    except Exception as e:
        return f"搜索文件内容失败: {e}"


# ---------------------------------------------------------------------------
# 服务构建
# ---------------------------------------------------------------------------

service = MCPService(
    name="mcp-filesystem",
    version="1.0.0",
    description="Manus 文件系统操作服务，提供工作区内的文件读写、编辑、搜索等能力",
)

service.register_tool(
    name="read_file",
    description="读取工作区内指定文件的内容",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "文件的相对路径，例如 report.md"},
        },
        "required": ["path"],
    },
    func=read_file,
)

service.register_tool(
    name="write_file",
    description="将内容写入工作区内的文件（覆盖写入，自动创建目录）",
    parameters={
        "type": "object",
        "properties": {
            "path":    {"type": "string", "description": "文件的相对路径"},
            "content": {"type": "string", "description": "要写入的文件内容"},
        },
        "required": ["path", "content"],
    },
    func=write_file,
)

service.register_tool(
    name="append_file",
    description="向工作区内的文件末尾追加内容",
    parameters={
        "type": "object",
        "properties": {
            "path":    {"type": "string", "description": "文件的相对路径"},
            "content": {"type": "string", "description": "要追加的内容"},
        },
        "required": ["path", "content"],
    },
    func=append_file,
)

service.register_tool(
    name="edit_file",
    description="精确替换文件中的指定内容片段",
    parameters={
        "type": "object",
        "properties": {
            "path":        {"type": "string", "description": "文件的相对路径"},
            "old_content": {"type": "string", "description": "要被替换的原始内容（必须与文件中完全一致）"},
            "new_content": {"type": "string", "description": "替换后的新内容"},
        },
        "required": ["path", "old_content", "new_content"],
    },
    func=edit_file,
)

service.register_tool(
    name="list_files",
    description="列出工作区内指定目录下的文件和子目录树",
    parameters={
        "type": "object",
        "properties": {
            "path": {"type": "string", "description": "要列出的目录路径，默认为根目录 '.'"},
        },
    },
    func=list_files,
)

service.register_tool(
    name="find_files",
    description="在工作区内按文件名模式（glob）查找文件",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "文件名匹配模式，例如 '*.py'"},
            "path":    {"type": "string", "description": "搜索起始目录，默认为 '.'"},
        },
        "required": ["pattern"],
    },
    func=find_files,
)

service.register_tool(
    name="grep_files",
    description="在工作区内按正则表达式搜索文件内容",
    parameters={
        "type": "object",
        "properties": {
            "pattern": {"type": "string", "description": "正则表达式搜索模式"},
            "path":    {"type": "string", "description": "搜索起始目录，默认为 '.'"},
        },
        "required": ["pattern"],
    },
    func=grep_files,
)

app = service.app

if __name__ == "__main__":
    port = int(os.environ.get("MCP_FILESYSTEM_PORT", "8101"))
    uvicorn.run(app, host="0.0.0.0", port=port)
