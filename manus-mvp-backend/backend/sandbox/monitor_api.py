"""
Docker 沙箱监控 API 端点

提供 REST API 和 WebSocket 实时推送：
- GET  /api/monitor/overview          系统总览
- GET  /api/monitor/containers        所有容器指标
- GET  /api/monitor/container/{name}  指定容器指标
- GET  /api/monitor/history/{name}    指定容器历史
- GET  /api/monitor/overview/history  系统总览历史
- GET  /api/monitor/alerts            告警历史
- GET  /api/monitor/alerts/active     活跃告警
- GET  /api/monitor/thresholds        告警阈值
- PUT  /api/monitor/thresholds/{metric}  更新告警阈值
- POST /api/monitor/alerts/clear      清除告警
- WS   /ws/monitor                    实时推送
"""

import asyncio
import json
import logging
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Query
from fastapi.responses import JSONResponse

from sandbox.monitor import sandbox_monitor

logger = logging.getLogger("sandbox.monitor_api")


def register_monitor_api(app: FastAPI):
    """注册监控 API 到 FastAPI 应用"""

    @app.on_event("startup")
    async def _start_monitor():
        await sandbox_monitor.start()
        logger.info("监控引擎已随应用启动")

    @app.on_event("shutdown")
    async def _stop_monitor():
        await sandbox_monitor.stop()

    # ---- REST API ----

    @app.get("/api/monitor/overview")
    async def monitor_overview():
        """系统总览"""
        return JSONResponse(content={
            "success": True,
            "data": sandbox_monitor.get_overview(),
        })

    @app.get("/api/monitor/containers")
    async def monitor_containers():
        """所有容器当前指标"""
        return JSONResponse(content={
            "success": True,
            "data": sandbox_monitor.get_all_containers(),
        })

    @app.get("/api/monitor/container/{container_name}")
    async def monitor_container(container_name: str):
        """指定容器当前指标"""
        metrics = sandbox_monitor.get_container_metrics(container_name)
        if metrics is None:
            return JSONResponse(status_code=404, content={
                "success": False,
                "error": f"容器 {container_name} 不存在",
            })
        return JSONResponse(content={
            "success": True,
            "data": metrics,
        })

    @app.get("/api/monitor/history/{container_name}")
    async def monitor_history(container_name: str, duration: int = Query(default=300, ge=10, le=3600)):
        """指定容器历史数据"""
        history = sandbox_monitor.get_container_history(container_name, duration)
        return JSONResponse(content={
            "success": True,
            "data": history,
            "count": len(history),
        })

    @app.get("/api/monitor/overview/history")
    async def monitor_overview_history(duration: int = Query(default=300, ge=10, le=3600)):
        """系统总览历史"""
        history = sandbox_monitor.get_overview_history(duration)
        return JSONResponse(content={
            "success": True,
            "data": history,
            "count": len(history),
        })

    @app.get("/api/monitor/alerts")
    async def monitor_alerts(limit: int = Query(default=50, ge=1, le=200)):
        """告警历史"""
        alerts = sandbox_monitor.get_alerts(limit)
        return JSONResponse(content={
            "success": True,
            "data": alerts,
            "count": len(alerts),
        })

    @app.get("/api/monitor/alerts/active")
    async def monitor_active_alerts():
        """当前活跃告警"""
        alerts = sandbox_monitor.get_active_alerts()
        return JSONResponse(content={
            "success": True,
            "data": alerts,
            "count": len(alerts),
        })

    @app.get("/api/monitor/thresholds")
    async def monitor_thresholds():
        """告警阈值配置"""
        return JSONResponse(content={
            "success": True,
            "data": sandbox_monitor.get_thresholds(),
        })

    @app.put("/api/monitor/thresholds/{metric}")
    async def update_threshold(
        metric: str,
        warning: Optional[float] = Query(default=None),
        critical: Optional[float] = Query(default=None),
    ):
        """更新告警阈值"""
        ok = sandbox_monitor.update_threshold(metric, warning, critical)
        if not ok:
            return JSONResponse(status_code=404, content={
                "success": False,
                "error": f"指标 {metric} 不存在",
            })
        return JSONResponse(content={
            "success": True,
            "data": sandbox_monitor.get_thresholds().get(metric),
        })

    @app.post("/api/monitor/alerts/clear")
    async def clear_alerts():
        """清除所有告警"""
        sandbox_monitor.clear_alerts()
        return JSONResponse(content={"success": True})

    # ---- WebSocket 实时推送 ----

    @app.websocket("/ws/monitor")
    async def ws_monitor(websocket: WebSocket):
        """WebSocket 实时监控数据推送"""
        await websocket.accept()
        queue = sandbox_monitor.subscribe_ws()
        logger.info("WebSocket 监控客户端已连接")

        try:
            while True:
                data = await queue.get()
                await websocket.send_json(data)
        except WebSocketDisconnect:
            logger.info("WebSocket 监控客户端已断开")
        except Exception as e:
            logger.error("WebSocket 监控异常: %s", e)
        finally:
            sandbox_monitor.unsubscribe_ws(queue)

    logger.info("监控 API 已注册")
