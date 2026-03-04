"""
图片上传服务 — 从 main.py 中抽取的图片解码、校验和持久化逻辑。
"""

import base64
import binascii
import mimetypes
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from config.settings import settings
from sandbox.filesystem import get_workspace_root


_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+);base64,(?P<data>.+)$"
)


def sanitize_upload_filename(raw_name: str) -> str:
    """清理文件名，去除不安全字符。"""
    name = Path(raw_name or "image").name
    safe = re.sub(r"[^A-Za-z0-9._-]+", "_", name).strip("._")
    return safe[:80] or "image"


def pick_upload_extension(filename: str, mime_type: str) -> str:
    """根据文件名和 MIME 类型推断扩展名。"""
    ext = Path(filename).suffix.lower()
    if ext in {
        ".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg",
        ".heic", ".heif", ".tiff", ".tif",
    }:
        return ext
    guessed = (mimetypes.guess_extension(mime_type or "") or "").lower()
    if guessed == ".jpe":
        return ".jpg"
    return guessed or ".bin"


def decode_image_data_url(data_url: str) -> Optional[tuple]:
    """解码 Base64 Data URL，返回 (mime_type, raw_bytes) 或 None。"""
    if not isinstance(data_url, str):
        return None
    text = data_url.strip()
    if not text:
        return None
    matched = _DATA_URL_RE.match(text)
    if not matched:
        return None
    mime_type = matched.group("mime").lower()
    if not mime_type.startswith("image/"):
        return None

    payload = matched.group("data")
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        return None
    if not raw or len(raw) > settings.upload.max_image_bytes:
        return None
    return mime_type, raw


def persist_uploaded_images(
    conversation_id: str,
    images: Any,
) -> List[Dict[str, Any]]:
    """将上传的图片持久化到工作目录，返回保存结果列表。"""
    if not images:
        return []
    try:
        workspace_root = Path(get_workspace_root(conversation_id)).resolve()
        upload_dir = (workspace_root / "uploads").resolve()
        upload_dir.mkdir(parents=True, exist_ok=True)
    except Exception:
        return []

    saved: List[Dict[str, Any]] = []
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    for index, image in enumerate(images[:settings.upload.max_image_count], start=1):
        decoded = decode_image_data_url(getattr(image, "data_url", ""))
        if not decoded:
            continue
        mime_type, raw = decoded

        filename_hint = sanitize_upload_filename(getattr(image, "name", "") or "image")
        mime_hint = (getattr(image, "mime_type", "") or "").strip().lower()
        if mime_hint.startswith("image/"):
            mime_type = mime_hint
        stem = Path(filename_hint).stem or "image"
        ext = pick_upload_extension(filename_hint, mime_type)
        filename = f"{timestamp}_{index:02d}_{stem}{ext}"
        target_path = (upload_dir / filename).resolve()

        # 路径穿越防护
        if upload_dir != target_path and upload_dir not in target_path.parents:
            continue
        try:
            target_path.write_bytes(raw)
        except Exception:
            continue

        saved.append({
            "name": filename_hint,
            "mime_type": mime_type,
            "size_bytes": len(raw),
            "path": f"uploads/{filename}",
        })

    return saved
