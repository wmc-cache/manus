"""
Docker 沙箱 API 扩展

为 FastAPI 应用添加沙箱管理 API 端点：
- GET  /api/sandbox/docker/status          - 获取 Docker 沙箱总体状态
- GET  /api/sandbox/docker/containers      - 列出所有沙箱容器
- GET  /api/sandbox/docker/container/{id}  - 获取指定容器详情
- POST /api/sandbox/docker/stop/{id}       - 休眠指定容器
- POST /api/sandbox/docker/start/{id}      - 唤醒指定容器
- DELETE /api/sandbox/docker/container/{id} - 销毁指定容器

使用方式：
    from sandbox.docker_api import register_docker_api
    register_docker_api(app)
"""

import logging
import os
from typing import Optional

from fastapi import FastAPI, HTTPException, Query

logger = logging.getLogger("sandbox.docker_api")

DOCKER_SANDBOX_ENABLED = os.environ.get(
    "MANUS_DOCKER_SANDBOX", "true"
).strip().lower() in ("true", "1", "yes")


def register_docker_api(app: FastAPI):
    """注册 Docker 沙箱管理 API 到 FastAPI 应用。"""

    if not DOCKER_SANDBOX_ENABLED:
        logger.info("Docker 沙箱已禁用，跳过 API 注册")
        return

    from sandbox.docker_sandbox import sandbox_manager
    from sandbox.docker_tools_adapter import get_sandbox_status, list_all_sandboxes

    @app.get("/api/sandbox/docker/status")
    async def docker_sandbox_status():
        """获取 Docker 沙箱总体状态"""
        try:
            sandboxes = await list_all_sandboxes()
            running = sum(1 for s in sandboxes if s.get("status") == "running")
            stopped = sum(1 for s in sandboxes if s.get("status") == "stopped")
            return {
                "enabled": True,
                "total_containers": len(sandboxes),
                "running": running,
                "stopped": stopped,
                "image": os.environ.get("MANUS_SANDBOX_IMAGE", "manus-sandbox:latest"),
                "network": os.environ.get("MANUS_SANDBOX_NETWORK", "manus-sandbox-net"),
                "mem_limit": os.environ.get("MANUS_CONTAINER_MEM_LIMIT", "512m"),
                "idle_timeout": int(os.environ.get("MANUS_CONTAINER_IDLE_TIMEOUT", "600")),
            }
        except Exception as e:
            return {"enabled": True, "error": str(e)}

    @app.get("/api/sandbox/docker/containers")
    async def list_docker_containers():
        """列出所有沙箱容器"""
        try:
            return {"containers": await list_all_sandboxes()}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.get("/api/sandbox/docker/container/{conversation_id}")
    async def get_docker_container(conversation_id: str):
        """获取指定容器详情"""
        try:
            status = await get_sandbox_status(conversation_id)
            if status.get("status") == "not_found":
                raise HTTPException(status_code=404, detail="容器不存在")
            return status
        except HTTPException:
            raise
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/sandbox/docker/stop/{conversation_id}")
    async def stop_docker_container(conversation_id: str):
        """休眠指定容器"""
        try:
            await sandbox_manager.stop_container(conversation_id)
            return {"ok": True, "action": "stopped", "conversation_id": conversation_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.post("/api/sandbox/docker/start/{conversation_id}")
    async def start_docker_container(conversation_id: str):
        """唤醒指定容器"""
        try:
            sandbox = await sandbox_manager.get_or_create(conversation_id)
            return {
                "ok": True,
                "action": "started",
                "conversation_id": conversation_id,
                "container_name": sandbox.container_name,
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.delete("/api/sandbox/docker/container/{conversation_id}")
    async def destroy_docker_container(conversation_id: str):
        """销毁指定容器（不删除 workspace 数据）"""
        try:
            await sandbox_manager.destroy_container(conversation_id)
            return {"ok": True, "action": "destroyed", "conversation_id": conversation_id}
        except Exception as e:
            raise HTTPException(status_code=500, detail=str(e))

    @app.on_event("startup")
    async def startup_docker_sandbox():
        """应用启动时初始化 Docker 沙箱管理器"""
        try:
            await sandbox_manager.initialize()
            logger.info("Docker 沙箱管理器已在应用启动时初始化")
        except Exception as e:
            logger.error("Docker 沙箱管理器初始化失败: %s", e)

    @app.on_event("shutdown")
    async def shutdown_docker_sandbox():
        """应用关闭时清理 Docker 沙箱"""
        try:
            await sandbox_manager.shutdown()
            logger.info("Docker 沙箱管理器已关闭")
        except Exception as e:
            logger.error("Docker 沙箱管理器关闭失败: %s", e)

    logger.info("Docker 沙箱 API 已注册")
