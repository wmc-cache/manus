"""文件系统服务 - 文件树和内容管理（支持会话隔离）"""
import os
import mimetypes
from typing import Dict, Any, List, Optional
from sandbox.event_bus import event_bus, SandboxEvent

WORKSPACE_BASE = "/tmp/manus_workspace"


def get_workspace_root(conversation_id: Optional[str] = None) -> str:
    """获取指定会话的 workspace 根目录"""
    if conversation_id:
        path = os.path.join(WORKSPACE_BASE, conversation_id)
    else:
        path = os.path.join(WORKSPACE_BASE, "_default")
    os.makedirs(path, exist_ok=True)
    return path


def _is_text_file(path: str) -> bool:
    """判断是否为文本文件"""
    text_extensions = {
        ".py", ".js", ".ts", ".tsx", ".jsx", ".html", ".css", ".json",
        ".md", ".txt", ".yaml", ".yml", ".toml", ".ini", ".cfg",
        ".sh", ".bash", ".zsh", ".env", ".csv", ".xml", ".sql",
        ".java", ".c", ".cpp", ".h", ".hpp", ".go", ".rs", ".rb",
        ".php", ".swift", ".kt", ".scala", ".r", ".R", ".lua",
        ".dockerfile", ".gitignore", ".editorconfig",
    }
    _, ext = os.path.splitext(path)
    if ext.lower() in text_extensions:
        return True
    mime, _ = mimetypes.guess_type(path)
    return mime is not None and mime.startswith("text/")


def _get_file_icon(name: str, is_dir: bool) -> str:
    """获取文件图标标识"""
    if is_dir:
        return "folder"
    ext = os.path.splitext(name)[1].lower()
    icon_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "react", ".jsx": "react", ".html": "html",
        ".css": "css", ".json": "json", ".md": "markdown",
        ".txt": "text", ".sh": "shell", ".yaml": "yaml",
        ".yml": "yaml", ".sql": "database", ".csv": "data",
        ".png": "image", ".jpg": "image", ".jpeg": "image",
        ".gif": "image", ".svg": "image", ".pdf": "pdf",
    }
    return icon_map.get(ext, "file")


def _get_language(name: str) -> str:
    """获取文件语言标识（用于代码高亮）"""
    ext = os.path.splitext(name)[1].lower()
    lang_map = {
        ".py": "python", ".js": "javascript", ".ts": "typescript",
        ".tsx": "typescriptreact", ".jsx": "javascriptreact",
        ".html": "html", ".css": "css", ".json": "json",
        ".md": "markdown", ".sh": "shell", ".yaml": "yaml",
        ".yml": "yaml", ".sql": "sql", ".xml": "xml",
        ".java": "java", ".c": "c", ".cpp": "cpp",
        ".go": "go", ".rs": "rust", ".rb": "ruby",
    }
    return lang_map.get(ext, "plaintext")


async def get_file_tree(conversation_id: Optional[str] = None, max_depth: int = 4) -> List[Dict[str, Any]]:
    """获取文件树结构（按会话隔离）"""
    root = get_workspace_root(conversation_id)

    def _scan(path: str, depth: int) -> List[Dict[str, Any]]:
        if depth > max_depth:
            return []
        items = []
        try:
            entries = sorted(os.listdir(path))
        except PermissionError:
            return []

        dirs = []
        files = []
        for name in entries:
            if name.startswith("."):
                continue
            full = os.path.join(path, name)
            if os.path.isdir(full):
                dirs.append(name)
            else:
                files.append(name)

        for name in dirs:
            full = os.path.join(path, name)
            rel = os.path.relpath(full, root)
            children = _scan(full, depth + 1)
            items.append({
                "name": name,
                "path": rel,
                "type": "directory",
                "icon": "folder",
                "children": children,
            })

        for name in files:
            full = os.path.join(path, name)
            rel = os.path.relpath(full, root)
            stat = os.stat(full)
            items.append({
                "name": name,
                "path": rel,
                "type": "file",
                "icon": _get_file_icon(name, False),
                "language": _get_language(name),
                "size": stat.st_size,
                "is_text": _is_text_file(full),
            })

        return items

    return _scan(root, 0)


async def read_file_content(path: str, conversation_id: Optional[str] = None) -> Dict[str, Any]:
    """读取文件内容（按会话隔离）"""
    root = get_workspace_root(conversation_id)
    full_path = path if os.path.isabs(path) else os.path.join(root, path)

    if not os.path.exists(full_path):
        return {"error": f"文件不存在: {path}"}

    if not _is_text_file(full_path):
        return {
            "path": path,
            "is_text": False,
            "size": os.path.getsize(full_path),
            "message": "二进制文件，无法预览",
        }

    try:
        with open(full_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        if len(content) > 50000:
            content = content[:50000] + "\n... [内容被截断]"

        name = os.path.basename(full_path)

        await event_bus.publish(SandboxEvent(
            "file_opened",
            {"path": path, "name": name, "language": _get_language(name), "content": content},
            window_id="editor",
            conversation_id=conversation_id,
        ))

        return {
            "path": path,
            "name": name,
            "content": content,
            "language": _get_language(name),
            "is_text": True,
            "size": os.path.getsize(full_path),
        }
    except Exception as e:
        return {"error": str(e)}


async def notify_file_change(path: str, action: str = "modified", conversation_id: Optional[str] = None):
    """通知文件变更（带 conversation_id）"""
    await event_bus.publish(SandboxEvent(
        "file_changed",
        {"path": path, "action": action},
        window_id="files",
        conversation_id=conversation_id,
    ))
