"""
Manus MCP 公共基础库
提供 MCP 服务的标准化接口、数据模型和工具注册机制
"""

import json
import logging
import traceback
from typing import Any, Callable, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 数据模型
# ---------------------------------------------------------------------------

class ToolParameter(BaseModel):
    """工具参数定义"""
    type: str
    description: str
    enum: Optional[List[str]] = None
    items: Optional[Dict[str, Any]] = None


class ToolDefinition(BaseModel):
    """工具定义（兼容 OpenAI Function Calling 格式）"""
    name: str
    description: str
    parameters: Dict[str, Any] = Field(default_factory=dict)


class ExecuteRequest(BaseModel):
    """工具执行请求"""
    tool_name: str
    arguments: Dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = None


class ExecuteResponse(BaseModel):
    """工具执行响应"""
    success: bool
    result: Optional[str] = None
    error: Optional[str] = None
    tool_name: str


class ServiceInfo(BaseModel):
    """MCP 服务信息"""
    name: str
    version: str
    description: str
    tools: List[ToolDefinition]


# ---------------------------------------------------------------------------
# MCP 服务基类
# ---------------------------------------------------------------------------

class MCPService:
    """MCP 服务基类，提供标准化的服务框架"""

    def __init__(self, name: str, version: str, description: str):
        self.name = name
        self.version = version
        self.description = description
        self._tools: Dict[str, Dict[str, Any]] = {}
        self.app = self._create_app()

    def _create_app(self) -> FastAPI:
        app = FastAPI(
            title=self.name,
            version=self.version,
            description=self.description,
        )

        app.add_middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        )

        # 注册路由
        @app.get("/.well-known/mcp.json", response_model=ServiceInfo)
        async def service_discovery():
            """MCP 服务发现端点"""
            return ServiceInfo(
                name=self.name,
                version=self.version,
                description=self.description,
                tools=[
                    ToolDefinition(
                        name=name,
                        description=meta["description"],
                        parameters=meta["parameters"],
                    )
                    for name, meta in self._tools.items()
                ],
            )

        @app.get("/tools")
        async def list_tools():
            """列出所有可用工具"""
            return {
                "tools": [
                    {
                        "name": name,
                        "description": meta["description"],
                        "parameters": meta["parameters"],
                    }
                    for name, meta in self._tools.items()
                ]
            }

        @app.post("/execute")
        async def execute_tool(request: ExecuteRequest):
            """执行工具"""
            return await self._handle_execute(request)

        @app.get("/health")
        async def health():
            return {"status": "ok", "service": self.name, "version": self.version}

        return app

    def register_tool(
        self,
        name: str,
        description: str,
        parameters: Dict[str, Any],
        func: Callable,
    ):
        """注册一个工具"""
        self._tools[name] = {
            "description": description,
            "parameters": parameters,
            "func": func,
        }
        logger.info("[%s] 已注册工具: %s", self.name, name)

    async def _handle_execute(self, request: ExecuteRequest) -> ExecuteResponse:
        """处理工具执行请求"""
        tool_name = request.tool_name
        if tool_name not in self._tools:
            return ExecuteResponse(
                success=False,
                error=f"未知工具: {tool_name}",
                tool_name=tool_name,
            )

        tool = self._tools[tool_name]
        func = tool["func"]

        try:
            import inspect
            sig = inspect.signature(func)
            kwargs = dict(request.arguments)

            # 注入 conversation_id（如果函数支持）
            if "conversation_id" in sig.parameters and request.conversation_id:
                kwargs["conversation_id"] = request.conversation_id

            result = await func(**kwargs)
            return ExecuteResponse(
                success=True,
                result=str(result) if result is not None else "",
                tool_name=tool_name,
            )
        except TypeError as e:
            error_msg = f"工具 `{tool_name}` 参数错误: {e}"
            logger.warning(error_msg)
            return ExecuteResponse(success=False, error=error_msg, tool_name=tool_name)
        except Exception as e:
            error_msg = f"工具 `{tool_name}` 执行失败: {e}"
            logger.error("%s\n%s", error_msg, traceback.format_exc())
            return ExecuteResponse(success=False, error=error_msg, tool_name=tool_name)
