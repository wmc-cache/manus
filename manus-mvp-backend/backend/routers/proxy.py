"""
反向代理路由 — 从 main.py 中抽取的沙箱端口代理逻辑。
"""

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, StreamingResponse

import httpx

from sandbox.port_expose import port_expose_manager



def _service_down_page(port: int) -> str:
    return f"""<!DOCTYPE html>
<html lang="zh">
<head>
<meta charset="utf-8">
<title>服务未运行</title>
<style>
  body{{margin:0;display:flex;align-items:center;justify-content:center;min-height:100vh;
       background:#0f1117;font-family:system-ui,sans-serif;color:#e2e8f0}}
  .box{{text-align:center;padding:40px;max-width:420px}}
  .icon{{font-size:56px;margin-bottom:16px}}
  h1{{font-size:20px;font-weight:600;margin:0 0 8px;color:#f1f5f9}}
  p{{font-size:14px;color:#94a3b8;line-height:1.6;margin:0 0 24px}}
  .port{{display:inline-block;background:#1e2330;border:1px solid #334155;
         border-radius:6px;padding:4px 12px;font-family:monospace;font-size:13px;color:#7dd3fc}}
  .hint{{font-size:12px;color:#475569;margin-top:16px}}
</style>
</head>
<body>
<div class="box">
  <div class="icon">⚙️</div>
  <h1>服务未运行</h1>
  <p>沙箱内端口 <span class="port">:{port}</span> 的服务已停止。<br>
     请返回对话，让 Agent 重新启动服务。</p>
  <p class="hint">提示：在对话框输入「重新启动服务」即可</p>
</div>
</body>
</html>"""


async def _try_auto_register(conversation_id: str, port: int):
    """后端重启后尝试按需重建 docker exec 隧道。"""
    try:
        from sandbox.docker_sandbox import _container_name
        import asyncio, subprocess
        container_name = _container_name(conversation_id)
        # 检查容器是否存在且运行中
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Running}}", container_name],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0 or result.stdout.strip() != "true":
            return None
        from sandbox.port_expose import docker_exec_proxy
        tunnel_key = f"{conversation_id}:{port}"
        local_port = await docker_exec_proxy.create_tunnel(container_name, port, tunnel_key)
        return port_expose_manager.expose(
            port=port,
            conversation_id=conversation_id,
            label=f"Port {port}",
            internal_host="127.0.0.1",
            target_port=local_port,
        )
    except Exception:
        return None

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
        # 尝试自动恢复：后端重启后 in-memory 状态丢失，按需重建 docker exec 隧道
        entry = await _try_auto_register(conversation_id, port)
    if not entry:
        return HTMLResponse(content=_service_down_page(port), status_code=502)

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

        if resp.status_code == 502 and not resp.content.strip():
            return HTMLResponse(
                content=_service_down_page(entry.port),
                status_code=502,
            )

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
