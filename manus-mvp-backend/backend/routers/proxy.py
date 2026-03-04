"""
反向代理路由 — 从 main.py 中抽取的沙箱端口代理逻辑。
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse

import httpx

from sandbox.port_expose import port_expose_manager


router = APIRouter(tags=["proxy"])


@router.api_route(
    "/proxy/{conversation_id}/{port}/{path:path}",
    methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS", "HEAD"],
)
async def proxy_to_sandbox(conversation_id: str, port: int, path: str, request: Request):
    """
    反向代理：将请求转发到沙箱容器内的 Web 服务。

    路径格式: /proxy/{conversation_id}/{port}/{path}
    例如: /proxy/_default/8080/index.html
    """
    entry = port_expose_manager.get(port, conversation_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"端口 {port} 未暴露（会话: {conversation_id}）")

    base_url = port_expose_manager.get_target_url(port, conversation_id)
    target_url = f"{base_url}/{path}"
    if request.url.query:
        target_url += f"?{request.url.query}"

    body = await request.body()

    hop_by_hop = {"connection", "keep-alive", "transfer-encoding", "te", "trailer", "upgrade", "host"}
    headers = {
        k: v for k, v in request.headers.items()
        if k.lower() not in hop_by_hop
    }
    actual_port = entry.target_port if entry.target_port else entry.port
    headers["host"] = f"{entry.internal_host}:{actual_port}"

    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            resp = await client.request(
                method=request.method,
                url=target_url,
                headers=headers,
                content=body,
            )

        excluded_headers = {"content-encoding", "content-length", "transfer-encoding", "connection"}
        response_headers = {
            k: v for k, v in resp.headers.items()
            if k.lower() not in excluded_headers
        }

        return StreamingResponse(
            content=iter([resp.content]),
            status_code=resp.status_code,
            headers=response_headers,
            media_type=resp.headers.get("content-type"),
        )
    except httpx.ConnectError:
        raise HTTPException(
            status_code=502,
            detail=f"无法连接到沙箱服务（端口 {entry.port}），请确认服务已启动",
        )
    except httpx.RemoteProtocolError:
        raise HTTPException(
            status_code=502,
            detail=f"沙箱容器未运行或服务（端口 {entry.port}）未启动，请重新运行服务后再试",
        )
    except httpx.TimeoutException:
        raise HTTPException(status_code=504, detail="代理请求超时")
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"代理错误: {str(e)}")
