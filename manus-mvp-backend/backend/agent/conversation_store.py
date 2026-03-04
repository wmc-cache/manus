"""
会话持久化管理 — 从 agent/core.py 中抽取的会话加载、保存和管理逻辑。

职责：
- 从磁盘加载/保存会话数据
- 创建、删除、查询会话
- 管理会话标题
- 管理会话级锁
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Dict, Optional

from config.settings import settings
from models.schemas import Conversation, MessageRole


logger = logging.getLogger(__name__)


class ConversationStore:
    """会话持久化存储管理器。"""

    def __init__(self, store_path: Optional[str] = None):
        self._store_path = Path(store_path or settings.agent.conversations_file)
        self._store_path.parent.mkdir(parents=True, exist_ok=True)
        self._save_fail_streak = 0
        self.conversations: Dict[str, Conversation] = {}
        self._conversation_locks: Dict[str, asyncio.Lock] = {}
        self._load_conversations()

    def get_conversation_lock(self, conversation_id: str) -> asyncio.Lock:
        """获取会话级串行锁，避免同一会话并发运行导致状态互相覆盖。"""
        lock = self._conversation_locks.get(conversation_id)
        if lock is None:
            lock = asyncio.Lock()
            self._conversation_locks[conversation_id] = lock
        return lock

    def _load_conversations(self):
        """从磁盘加载历史会话"""
        if not self._store_path.exists():
            return

        try:
            payload = json.loads(self._store_path.read_text(encoding="utf-8"))
            items = payload.get("conversations", [])
            loaded: Dict[str, Conversation] = {}
            repaired = False
            for raw in items:
                conv = Conversation.model_validate(raw)
                if self._maybe_refresh_conversation_title(conv):
                    repaired = True
                loaded[conv.id] = conv
            self.conversations = loaded
            if repaired:
                self.save_conversations()
        except Exception:
            self.conversations = {}

    def save_conversations(self):
        """将会话持久化到磁盘"""
        try:
            data = {
                "conversations": [
                    conv.model_dump(mode="json")
                    for conv in self.conversations.values()
                ]
            }
            tmp_path = self._store_path.with_suffix(".tmp")
            tmp_path.write_text(
                json.dumps(data, ensure_ascii=False, indent=2),
                encoding="utf-8"
            )
            tmp_path.replace(self._store_path)
            if self._save_fail_streak > 0:
                logger.info(
                    "Conversation persistence recovered after %d failures.",
                    self._save_fail_streak,
                )
            self._save_fail_streak = 0
        except Exception as exc:
            self._save_fail_streak += 1
            logger.warning(
                "Failed to persist conversations to %s (streak=%d): %s",
                self._store_path,
                self._save_fail_streak,
                exc,
            )
            if self._save_fail_streak >= 3:
                logger.error(
                    "ALERT: conversation persistence failed %d consecutive times.",
                    self._save_fail_streak,
                )

    def get_or_create_conversation(self, conversation_id: Optional[str] = None) -> Conversation:
        """获取或创建对话"""
        if conversation_id and conversation_id in self.conversations:
            conv = self.conversations[conversation_id]
            if self._maybe_refresh_conversation_title(conv):
                self.save_conversations()
            return conv
        conv = Conversation()
        self.conversations[conv.id] = conv
        self.save_conversations()
        return conv

    async def delete_conversation(self, conversation_id: str) -> tuple[bool, str]:
        """删除会话。若会话正在执行，返回 busy。"""
        if conversation_id not in self.conversations:
            return False, "not_found"

        lock = self.get_conversation_lock(conversation_id)
        if lock.locked():
            return False, "busy"

        async with lock:
            if conversation_id not in self.conversations:
                return False, "not_found"
            del self.conversations[conversation_id]
            self.save_conversations()

        return True, "deleted"

    @staticmethod
    def _normalize_conversation_title(text: str) -> str:
        normalized = " ".join((text or "").strip().split())
        if not normalized:
            return settings.agent.default_conversation_title
        if len(normalized) <= settings.agent.max_conversation_title_chars:
            return normalized
        return normalized[:settings.agent.max_conversation_title_chars].rstrip() + "…"

    def _maybe_refresh_conversation_title(
        self,
        conversation: Conversation,
        preferred_source: Optional[str] = None,
    ) -> bool:
        """尝试用首条用户消息回填默认标题。"""
        current = (conversation.title or "").strip()
        if current and current != settings.agent.default_conversation_title:
            return False

        source = (preferred_source or "").strip()
        if not source:
            for msg in conversation.messages:
                if msg.role == MessageRole.USER and msg.content.strip():
                    source = msg.content
                    break
        if not source:
            return False

        next_title = self._normalize_conversation_title(source)
        if next_title == current:
            return False
        conversation.title = next_title
        return True

    def repair_conversation_titles(self) -> bool:
        """回填旧会话标题：默认标题但有首条用户消息时自动修复。"""
        repaired = False
        for conv in self.conversations.values():
            if self._maybe_refresh_conversation_title(conv):
                repaired = True
        if repaired:
            self.save_conversations()
        return repaired
