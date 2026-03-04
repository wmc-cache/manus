"""
Manus MVP — 应用入口（重构后）。

重构说明：
- 路由已拆分到 routers/ 目录（chat、sandbox、websocket、proxy）
- 认证中间件已拆分到 middleware/auth.py
- 上传服务已拆分到 services/upload.py
- 配置已集中到 config/settings.py
- 本文件仅负责：应用创建、中间件注册、路由挂载、启动入口
"""

import sys
import os
import logging

# 配置日志，确保自定义 logger 的输出可见
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
    stream=sys.stderr,
    force=True,
)

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config.settings import settings
from middleware.auth import auth_middleware

# Docker 沙箱补丁
try:
    from agent.tools_docker_patch import apply_docker_sandbox_patch
    apply_docker_sandbox_patch()
except Exception as _docker_patch_err:
    logging.getLogger("main").warning(
        "无法应用 Docker 沙箱补丁，将使用进程级沙箱: %s", _docker_patch_err
    )

# ---------------------------------------------------------------------------
# 创建 FastAPI 应用
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Manus MVP",
    description="AI Agent 系统 MVP - 含计算机窗口（会话隔离）",
    version=settings.server.version,
)

# ---------------------------------------------------------------------------
# CORS 配置
# ---------------------------------------------------------------------------

_allow_origins = settings.server.allowed_origins
_allow_origin_regex = settings.server.allowed_origin_regex or None
_allow_credentials = "*" not in _allow_origins

app.add_middleware(
    CORSMiddleware,
    allow_origins=_allow_origins,
    allow_origin_regex=None if "*" in _allow_origins else _allow_origin_regex,
    allow_credentials=_allow_credentials,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# 认证中间件
# ---------------------------------------------------------------------------

app.middleware("http")(auth_middleware)

# ---------------------------------------------------------------------------
# 注册路由
# ---------------------------------------------------------------------------

from routers.chat import router as chat_router
from routers.sandbox import router as sandbox_router
from routers.websocket import router as ws_router
from routers.proxy import router as proxy_router

app.include_router(chat_router)
app.include_router(sandbox_router)
app.include_router(ws_router)
app.include_router(proxy_router)

# ---------------------------------------------------------------------------
# 监控 API（可选）
# ---------------------------------------------------------------------------

try:
    from sandbox.monitor_api import register_monitor_api
    register_monitor_api(app)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# 启动入口
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host=settings.server.host, port=settings.server.port)
