"""事件总线 - 统一管理计算机窗口事件分发（支持会话隔离）"""
import asyncio
import json
from typing import Dict, Set, Any, Optional, List
from datetime import datetime


class SandboxEvent:
    """沙箱事件 - 带 conversation_id 标记"""
    def __init__(
        self,
        event_type: str,
        data: Dict[str, Any],
        window_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ):
        self.event_type = event_type
        self.data = data
        self.window_id = window_id
        self.conversation_id = conversation_id
        self.timestamp = datetime.now().isoformat()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "type": self.event_type,
            "data": self.data,
            "window_id": self.window_id,
            "conversation_id": self.conversation_id,
            "timestamp": self.timestamp,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


class EventBus:
    """事件总线 - 管理 WebSocket 订阅和事件分发（按 conversation_id 隔离）"""

    def __init__(self):
        self._queues: Set[asyncio.Queue] = set()
        # 按 conversation_id 存储事件历史
        self._history: Dict[str, List[dict]] = {}
        self._max_history_per_conv = 100

    def subscribe(self) -> asyncio.Queue:
        """订阅事件流"""
        queue = asyncio.Queue(maxsize=200)
        self._queues.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue):
        """取消订阅"""
        self._queues.discard(queue)

    async def publish(self, event: SandboxEvent):
        """发布事件到所有订阅者"""
        event_dict = event.to_dict()

        # 按 conversation_id 存储历史
        conv_id = event.conversation_id or "_global"
        if conv_id not in self._history:
            self._history[conv_id] = []
        self._history[conv_id].append(event_dict)
        if len(self._history[conv_id]) > self._max_history_per_conv:
            self._history[conv_id] = self._history[conv_id][-self._max_history_per_conv:]

        dead_queues = set()
        for queue in self._queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                dead_queues.add(queue)

        for q in dead_queues:
            self._queues.discard(q)

    def get_history(self, conversation_id: Optional[str] = None) -> list:
        """获取事件历史（可按 conversation_id 过滤）"""
        if conversation_id:
            return self._history.get(conversation_id, []).copy()
        # 返回所有历史
        all_events = []
        for events in self._history.values():
            all_events.extend(events)
        all_events.sort(key=lambda e: e.get("timestamp", ""))
        return all_events[-100:]


# 全局事件总线实例
event_bus = EventBus()
