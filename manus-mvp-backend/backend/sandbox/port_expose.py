"""
端口暴露服务 - 将沙箱容器内的 Web 服务代理到宿主机可访问的 URL

架构设计：
- Agent 调用 expose_port 工具后，在后端注册一个端口映射
- 后端通过反向代理将 /proxy/{port}/ 的请求转发到沙箱容器的对应端口
- 支持 Docker 模式（通过容器名访问）和进程模式（通过 localhost 访问）
- 前端通过 WebSocket 事件收到暴露的 URL，展示为可点击链接

网络路径：
  用户浏览器 -> 前端(3000) -> Vite Proxy -> 后端(8000) -> 沙箱容器(target_port)
"""

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger("sandbox.port_expose")


@dataclass
class ExposedPort:
    """表示一个已暴露的端口。"""
    port: int
    conversation_id: str
    label: str = ""
    created_at: float = field(default_factory=time.time)
    # 容器内部地址（Docker 模式下为容器名，进程模式下为 localhost）
    internal_host: str = "localhost"


class PortExposeManager:
    """
    管理所有已暴露的端口映射。
    
    核心职责：
    1. 注册/注销端口暴露
    2. 根据 conversation_id 和 port 解析内部地址
    3. 提供反向代理所需的目标 URL
    """

    def __init__(self):
        # key: "{conversation_id}:{port}"
        self._exposed: Dict[str, ExposedPort] = {}

    def expose(
        self,
        port: int,
        conversation_id: Optional[str] = None,
        label: str = "",
        internal_host: str = "localhost",
    ) -> ExposedPort:
        """注册一个端口暴露。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"

        entry = ExposedPort(
            port=port,
            conversation_id=cid,
            label=label or f"Port {port}",
            internal_host=internal_host,
        )
        self._exposed[key] = entry
        logger.info(
            "端口已暴露: %s:%d -> %s:%d (label=%s)",
            cid, port, internal_host, port, label,
        )
        return entry

    def unexpose(self, port: int, conversation_id: Optional[str] = None):
        """注销一个端口暴露。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"
        removed = self._exposed.pop(key, None)
        if removed:
            logger.info("端口已关闭: %s:%d", cid, port)

    def get(self, port: int, conversation_id: Optional[str] = None) -> Optional[ExposedPort]:
        """获取指定端口的暴露信息。"""
        cid = conversation_id or "_default"
        key = f"{cid}:{port}"
        return self._exposed.get(key)

    def list_exposed(self, conversation_id: Optional[str] = None) -> List[ExposedPort]:
        """列出指定会话的所有已暴露端口。"""
        cid = conversation_id or "_default"
        return [
            ep for ep in self._exposed.values()
            if ep.conversation_id == cid
        ]

    def list_all(self) -> List[ExposedPort]:
        """列出所有已暴露端口。"""
        return list(self._exposed.values())

    def get_target_url(self, port: int, conversation_id: Optional[str] = None) -> Optional[str]:
        """获取反向代理的目标 URL。"""
        entry = self.get(port, conversation_id)
        if not entry:
            return None
        return f"http://{entry.internal_host}:{entry.port}"

    def resolve_internal_host(self, conversation_id: Optional[str] = None) -> str:
        """
        解析沙箱容器的内部主机名。
        
        - Docker bridge 模式：使用容器名（如 manus-sandbox-_default）
        - Docker host 模式：使用 localhost（容器共享宿主机网络栈）
        - 进程模式：使用 localhost
        """
        docker_enabled = os.environ.get(
            "MANUS_DOCKER_SANDBOX", "true"
        ).strip().lower() in ("true", "1", "yes")

        if not docker_enabled:
            return "localhost"

        # 尝试获取 Docker 沙箱管理器的网络模式
        try:
            from sandbox.docker_sandbox import sandbox_manager, _container_name
            cid = conversation_id or "_default"

            if sandbox_manager._network_mode == "host":
                # host 模式下容器共享宿主机网络，直接用 localhost
                return "localhost"
            elif sandbox_manager._network_mode == "bridge":
                # bridge 模式下使用容器名
                return _container_name(cid)
            else:
                return "localhost"
        except Exception:
            return "localhost"


# 全局实例
port_expose_manager = PortExposeManager()
