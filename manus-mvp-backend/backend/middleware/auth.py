"""
认证中间件 — 从 main.py 中抽取的 HTTP/WebSocket 鉴权逻辑。
"""

from fastapi import Request, WebSocket
from fastapi.responses import JSONResponse
from config.settings import settings


def _extract_bearer_token(header_value: str) -> str:
    """从 Authorization 头中提取 Bearer Token。"""
    if not isinstance(header_value, str):
        return ""
    text = header_value.strip()
    if not text.lower().startswith("bearer "):
        return ""
    return text[7:].strip()


def is_authorized_http(request: Request) -> bool:
    """检查 HTTP 请求是否已通过认证。"""
    token = settings.server.api_token
    if not token:
        return True
    auth_token = _extract_bearer_token(request.headers.get("authorization", ""))
    return bool(auth_token) and auth_token == token


def is_authorized_ws(websocket: WebSocket) -> bool:
    """检查 WebSocket 连接是否已通过认证。"""
    token = settings.server.api_token
    if not token:
        return True
    header_token = _extract_bearer_token(websocket.headers.get("authorization", ""))
    query_token = websocket.query_params.get("token", "").strip()
    effective_token = header_token or query_token
    return bool(effective_token) and effective_token == token


async def auth_middleware(request: Request, call_next):
    """FastAPI HTTP 中间件：拦截业务 API 进行鉴权。"""
    # 代理路径和健康检查无需鉴权
    if request.url.path.startswith("/proxy/"):
        return await call_next(request)
    if request.url.path.startswith("/api/") and request.url.path != "/api/health":
        if not is_authorized_http(request):
            return JSONResponse(status_code=401, content={"detail": "Unauthorized"})
    return await call_next(request)
