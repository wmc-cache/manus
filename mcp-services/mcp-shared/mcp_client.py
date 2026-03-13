"""
Manus MCP 客户端
Agent 通过此客户端发现并调用各 MCP 工具服务
"""

import asyncio
import json
import logging
import os
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# MCP 服务地址配置（从环境变量读取，支持 Docker Compose 服务名）
MCP_FILESYSTEM_URL = os.environ.get("MCP_FILESYSTEM_URL", "http://localhost:8101")
MCP_EXECUTION_URL  = os.environ.get("MCP_EXECUTION_URL",  "http://localhost:8102")
MCP_BROWSER_URL    = os.environ.get("MCP_BROWSER_URL",    "http://localhost:8103")
MCP_RESEARCH_URL   = os.environ.get("MCP_RESEARCH_URL",   "http://localhost:8104")

# 工具名 -> MCP 服务 URL 的路由表
TOOL_SERVICE_MAP: Dict[str, str] = {
    # 文件系统工具
    "read_file":    MCP_FILESYSTEM_URL,
    "write_file":   MCP_FILESYSTEM_URL,
    "edit_file":    MCP_FILESYSTEM_URL,
    "append_file":  MCP_FILESYSTEM_URL,
    "list_files":   MCP_FILESYSTEM_URL,
    "find_files":   MCP_FILESYSTEM_URL,
    "grep_files":   MCP_FILESYSTEM_URL,
    # 代码执行工具
    "shell_exec":    MCP_EXECUTION_URL,
    "execute_code":  MCP_EXECUTION_URL,
    "expose_port":   MCP_EXECUTION_URL,
    # 浏览器工具
    "browser_navigate":    MCP_BROWSER_URL,
    "browser_screenshot":  MCP_BROWSER_URL,
    "browser_get_content": MCP_BROWSER_URL,
    "browser_click":       MCP_BROWSER_URL,
    "browser_input":       MCP_BROWSER_URL,
    "browser_scroll":      MCP_BROWSER_URL,
    # 搜索研究工具
    "web_search":       MCP_RESEARCH_URL,
    "wide_research":    MCP_RESEARCH_URL,
    "spawn_sub_agents": MCP_RESEARCH_URL,
    "data_analysis":    MCP_RESEARCH_URL,
}

# HTTP 客户端超时配置
REQUEST_TIMEOUT = float(os.environ.get("MCP_REQUEST_TIMEOUT", "120"))


class MCPClient:
    """
    MCP 工具客户端
    负责将 Agent 的工具调用请求路由到对应的 MCP 服务
    """

    def __init__(self):
        self._http_client: Optional[httpx.AsyncClient] = None
        self._tool_cache: Dict[str, List[Dict]] = {}  # 缓存各服务的工具定义

    def _get_http_client(self) -> httpx.AsyncClient:
        if self._http_client is None or self._http_client.is_closed:
            self._http_client = httpx.AsyncClient(
                timeout=REQUEST_TIMEOUT,
                limits=httpx.Limits(max_keepalive_connections=20, max_connections=100),
            )
        return self._http_client

    async def close(self):
        if self._http_client and not self._http_client.is_closed:
            await self._http_client.aclose()

    def get_service_url(self, tool_name: str) -> Optional[str]:
        """根据工具名获取对应 MCP 服务的 URL"""
        return TOOL_SERVICE_MAP.get(tool_name)

    async def execute_tool(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        conversation_id: Optional[str] = None,
    ) -> str:
        """
        调用 MCP 工具服务执行指定工具
        返回工具执行结果字符串
        """
        service_url = self.get_service_url(tool_name)
        if not service_url:
            return f"未知工具: {tool_name}（未找到对应的 MCP 服务）"

        payload = {
            "tool_name": tool_name,
            "arguments": arguments,
            "conversation_id": conversation_id,
        }

        try:
            client = self._get_http_client()
            response = await client.post(
                f"{service_url}/execute",
                json=payload,
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
            data = response.json()

            if data.get("success"):
                return data.get("result", "")
            else:
                error = data.get("error", "未知错误")
                logger.warning("[MCPClient] 工具 %s 执行失败: %s", tool_name, error)
                return f"工具执行失败: {error}"

        except httpx.ConnectError:
            msg = f"无法连接到工具服务 {service_url}（工具: {tool_name}），请确认服务已启动。"
            logger.error("[MCPClient] %s", msg)
            return msg
        except httpx.TimeoutException:
            msg = f"工具 {tool_name} 执行超时（>{REQUEST_TIMEOUT}s）"
            logger.error("[MCPClient] %s", msg)
            return msg
        except Exception as e:
            msg = f"调用 MCP 服务出错（工具: {tool_name}）: {e}"
            logger.error("[MCPClient] %s", msg)
            return msg

    async def discover_tools(self, service_url: str) -> List[Dict]:
        """从指定 MCP 服务发现工具定义"""
        if service_url in self._tool_cache:
            return self._tool_cache[service_url]
        try:
            client = self._get_http_client()
            response = await client.get(f"{service_url}/.well-known/mcp.json", timeout=10)
            response.raise_for_status()
            data = response.json()
            tools = data.get("tools", [])
            self._tool_cache[service_url] = tools
            return tools
        except Exception as e:
            logger.warning("[MCPClient] 无法从 %s 发现工具: %s", service_url, e)
            return []

    async def discover_all_tools(self) -> List[Dict]:
        """从所有已注册的 MCP 服务发现工具定义"""
        seen_urls = set(TOOL_SERVICE_MAP.values())
        tasks = [self.discover_tools(url) for url in seen_urls]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        all_tools = []
        for result in results:
            if isinstance(result, list):
                all_tools.extend(result)
        return all_tools

    async def health_check(self) -> Dict[str, str]:
        """检查所有 MCP 服务的健康状态"""
        seen_urls = set(TOOL_SERVICE_MAP.values())
        status = {}
        for url in seen_urls:
            try:
                client = self._get_http_client()
                response = await client.get(f"{url}/health", timeout=5)
                if response.status_code == 200:
                    status[url] = "ok"
                else:
                    status[url] = f"error:{response.status_code}"
            except Exception as e:
                status[url] = f"unreachable:{e}"
        return status


# 全局 MCP 客户端单例
mcp_client = MCPClient()
