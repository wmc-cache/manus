"""
Docker 沙箱监控与告警引擎

功能：
1. 定时采集所有沙箱容器的资源使用数据（CPU、内存、网络 I/O、磁盘 I/O、PID 数）
2. 维护时间序列历史数据（环形缓冲区，默认保留 1 小时）
3. 基于可配置阈值的告警引擎（支持多级告警：info/warning/critical）
4. 告警去重与冷却机制，避免告警风暴
5. 提供 API 数据接口供前端仪表盘消费
"""

import asyncio
import json
import logging
import os
import time
from collections import deque
from dataclasses import dataclass, field, asdict
from enum import Enum
from typing import Any, Deque, Dict, List, Optional, Tuple

logger = logging.getLogger("sandbox.monitor")

# ---------------------------------------------------------------------------
# 配置
# ---------------------------------------------------------------------------

# 采集间隔（秒）
COLLECT_INTERVAL = int(os.environ.get("MANUS_MONITOR_INTERVAL", "5"))

# 历史数据保留时长（秒）
HISTORY_RETENTION = int(os.environ.get("MANUS_MONITOR_HISTORY", "3600"))

# 最大历史数据点数
MAX_HISTORY_POINTS = HISTORY_RETENTION // max(COLLECT_INTERVAL, 1)

# 告警冷却时间（秒）- 同一告警在冷却期内不重复触发
ALERT_COOLDOWN = int(os.environ.get("MANUS_ALERT_COOLDOWN", "60"))

# 最大告警历史条数
MAX_ALERT_HISTORY = 200

# ---------------------------------------------------------------------------
# 告警阈值配置
# ---------------------------------------------------------------------------

class AlertLevel(str, Enum):
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


@dataclass
class AlertThreshold:
    """告警阈值定义"""
    metric: str          # 指标名称
    warning: float       # warning 阈值
    critical: float      # critical 阈值
    unit: str = ""       # 单位
    description: str = ""


# 默认告警阈值
DEFAULT_THRESHOLDS: Dict[str, AlertThreshold] = {
    "cpu_percent": AlertThreshold(
        metric="cpu_percent",
        warning=float(os.environ.get("MANUS_ALERT_CPU_WARNING", "70")),
        critical=float(os.environ.get("MANUS_ALERT_CPU_CRITICAL", "90")),
        unit="%",
        description="CPU 使用率",
    ),
    "memory_percent": AlertThreshold(
        metric="memory_percent",
        warning=float(os.environ.get("MANUS_ALERT_MEM_WARNING", "75")),
        critical=float(os.environ.get("MANUS_ALERT_MEM_CRITICAL", "90")),
        unit="%",
        description="内存使用率",
    ),
    "memory_usage_mb": AlertThreshold(
        metric="memory_usage_mb",
        warning=float(os.environ.get("MANUS_ALERT_MEM_MB_WARNING", "384")),
        critical=float(os.environ.get("MANUS_ALERT_MEM_MB_CRITICAL", "480")),
        unit="MB",
        description="内存使用量",
    ),
    "pids": AlertThreshold(
        metric="pids",
        warning=float(os.environ.get("MANUS_ALERT_PIDS_WARNING", "192")),
        critical=float(os.environ.get("MANUS_ALERT_PIDS_CRITICAL", "240")),
        unit="",
        description="进程数",
    ),
}


# ---------------------------------------------------------------------------
# 数据结构
# ---------------------------------------------------------------------------

@dataclass
class ContainerMetrics:
    """单个容器的资源使用指标"""
    timestamp: float = 0.0
    container_id: str = ""
    container_name: str = ""
    conversation_id: str = ""
    status: str = "unknown"

    # CPU
    cpu_percent: float = 0.0

    # 内存
    memory_usage_bytes: int = 0
    memory_limit_bytes: int = 0
    memory_percent: float = 0.0
    memory_usage_mb: float = 0.0

    # 网络 I/O
    net_rx_bytes: int = 0
    net_tx_bytes: int = 0
    net_rx_rate: float = 0.0   # bytes/s
    net_tx_rate: float = 0.0   # bytes/s

    # 磁盘 I/O
    disk_read_bytes: int = 0
    disk_write_bytes: int = 0
    disk_read_rate: float = 0.0   # bytes/s
    disk_write_rate: float = 0.0  # bytes/s

    # PID
    pids: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class AlertEvent:
    """告警事件"""
    timestamp: float
    container_name: str
    conversation_id: str
    metric: str
    value: float
    threshold: float
    level: str  # info / warning / critical
    message: str
    resolved: bool = False
    resolved_at: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        if self.resolved_at:
            d["resolved_at_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.resolved_at))
        return d


@dataclass
class SystemOverview:
    """系统总览"""
    timestamp: float = 0.0
    total_containers: int = 0
    running_containers: int = 0
    stopped_containers: int = 0
    total_cpu_percent: float = 0.0
    total_memory_mb: float = 0.0
    total_memory_limit_mb: float = 0.0
    total_net_rx_rate: float = 0.0
    total_net_tx_rate: float = 0.0
    active_alerts: int = 0
    uptime_seconds: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["timestamp_str"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.timestamp))
        return d


# ---------------------------------------------------------------------------
# 监控引擎
# ---------------------------------------------------------------------------

class SandboxMonitor:
    """Docker 沙箱监控与告警引擎"""

    def __init__(self):
        self._started = False
        self._start_time = 0.0
        self._task: Optional[asyncio.Task] = None

        # 当前快照
        self._current_metrics: Dict[str, ContainerMetrics] = {}
        self._current_overview = SystemOverview()

        # 历史时间序列（每个容器一个 deque）
        self._history: Dict[str, Deque[ContainerMetrics]] = {}
        # 系统总览历史
        self._overview_history: Deque[SystemOverview] = deque(maxlen=MAX_HISTORY_POINTS)

        # 告警
        self._thresholds = dict(DEFAULT_THRESHOLDS)
        self._alerts: Deque[AlertEvent] = deque(maxlen=MAX_ALERT_HISTORY)
        self._active_alerts: Dict[str, AlertEvent] = {}  # key = container_name:metric
        self._alert_cooldown: Dict[str, float] = {}  # key -> last alert time

        # 上一次采集的网络/磁盘数据（用于计算速率）
        self._prev_net: Dict[str, Tuple[int, int, float]] = {}  # container -> (rx, tx, ts)
        self._prev_disk: Dict[str, Tuple[int, int, float]] = {}

        # WebSocket 订阅者
        self._ws_subscribers: List[asyncio.Queue] = []

    async def start(self):
        """启动监控循环"""
        if self._started:
            return
        self._started = True
        self._start_time = time.time()
        self._task = asyncio.create_task(self._collect_loop())
        logger.info("监控引擎已启动 (采集间隔=%ds, 历史保留=%ds)", COLLECT_INTERVAL, HISTORY_RETENTION)

    async def stop(self):
        """停止监控循环"""
        self._started = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("监控引擎已停止")

    def subscribe_ws(self) -> asyncio.Queue:
        """订阅实时推送"""
        q: asyncio.Queue = asyncio.Queue(maxsize=50)
        self._ws_subscribers.append(q)
        return q

    def unsubscribe_ws(self, q: asyncio.Queue):
        """取消订阅"""
        if q in self._ws_subscribers:
            self._ws_subscribers.remove(q)

    async def _broadcast_ws(self, data: Dict):
        """向所有 WebSocket 订阅者推送数据"""
        dead = []
        for q in self._ws_subscribers:
            try:
                q.put_nowait(data)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._ws_subscribers.remove(q)

    # ----- 数据采集 -----

    async def _collect_loop(self):
        """定时采集循环"""
        logger.info("采集循环已启动，开始首次采集...")
        while self._started:
            try:
                await self._collect_once()
            except Exception as e:
                logger.error("监控采集异常: %s", e, exc_info=True)
            await asyncio.sleep(COLLECT_INTERVAL)

    async def _collect_once(self):
        """执行一次完整采集"""
        now = time.time()

        # 获取所有 manus-sandbox 容器
        containers = await self._list_sandbox_containers()
        logger.debug("采集到 %d 个容器: %s", len(containers), [c.get('name') for c in containers])

        new_metrics: Dict[str, ContainerMetrics] = {}
        total_cpu = 0.0
        total_mem_mb = 0.0
        total_mem_limit_mb = 0.0
        total_net_rx = 0.0
        total_net_tx = 0.0
        running = 0
        stopped = 0

        for cinfo in containers:
            cname = cinfo["name"]
            cid = cinfo["id"]
            status = cinfo["status"]
            conv_id = cname.replace("manus-sandbox-", "") if cname.startswith("manus-sandbox-") else cname

            metrics = ContainerMetrics(
                timestamp=now,
                container_id=cid,
                container_name=cname,
                conversation_id=conv_id,
                status=status,
            )

            is_running = "up" in status.lower() or "running" in status.lower()
            if is_running:
                running += 1
                stats = await self._get_container_stats(cid)
                if stats:
                    metrics.cpu_percent = stats.get("cpu_percent", 0.0)
                    metrics.memory_usage_bytes = stats.get("memory_usage", 0)
                    metrics.memory_limit_bytes = stats.get("memory_limit", 0)
                    metrics.memory_usage_mb = round(metrics.memory_usage_bytes / (1024 * 1024), 2)
                    if metrics.memory_limit_bytes > 0:
                        metrics.memory_percent = round(
                            metrics.memory_usage_bytes / metrics.memory_limit_bytes * 100, 2
                        )

                    # 网络 I/O
                    rx = stats.get("net_rx_bytes", 0)
                    tx = stats.get("net_tx_bytes", 0)
                    if cname in self._prev_net:
                        prev_rx, prev_tx, prev_ts = self._prev_net[cname]
                        dt = now - prev_ts
                        if dt > 0:
                            metrics.net_rx_rate = round((rx - prev_rx) / dt, 2)
                            metrics.net_tx_rate = round((tx - prev_tx) / dt, 2)
                    metrics.net_rx_bytes = rx
                    metrics.net_tx_bytes = tx
                    self._prev_net[cname] = (rx, tx, now)

                    # 磁盘 I/O
                    dr = stats.get("disk_read_bytes", 0)
                    dw = stats.get("disk_write_bytes", 0)
                    if cname in self._prev_disk:
                        prev_dr, prev_dw, prev_ts = self._prev_disk[cname]
                        dt = now - prev_ts
                        if dt > 0:
                            metrics.disk_read_rate = round((dr - prev_dr) / dt, 2)
                            metrics.disk_write_rate = round((dw - prev_dw) / dt, 2)
                    metrics.disk_read_bytes = dr
                    metrics.disk_write_bytes = dw
                    self._prev_disk[cname] = (dr, dw, now)

                    # PID
                    metrics.pids = stats.get("pids", 0)

                    total_cpu += metrics.cpu_percent
                    total_mem_mb += metrics.memory_usage_mb
                    total_mem_limit_mb += metrics.memory_limit_bytes / (1024 * 1024)
                    total_net_rx += metrics.net_rx_rate
                    total_net_tx += metrics.net_tx_rate
            else:
                stopped += 1

            new_metrics[cname] = metrics

            # 存入历史
            if cname not in self._history:
                self._history[cname] = deque(maxlen=MAX_HISTORY_POINTS)
            self._history[cname].append(metrics)

            # 告警检查
            if is_running:
                self._check_alerts(metrics)

        self._current_metrics = new_metrics

        # 系统总览
        overview = SystemOverview(
            timestamp=now,
            total_containers=len(containers),
            running_containers=running,
            stopped_containers=stopped,
            total_cpu_percent=round(total_cpu, 2),
            total_memory_mb=round(total_mem_mb, 2),
            total_memory_limit_mb=round(total_mem_limit_mb, 2),
            total_net_rx_rate=round(total_net_rx, 2),
            total_net_tx_rate=round(total_net_tx, 2),
            active_alerts=len(self._active_alerts),
            uptime_seconds=round(now - self._start_time, 1),
        )
        self._current_overview = overview
        self._overview_history.append(overview)

        # 清理已不存在容器的历史数据
        active_names = set(new_metrics.keys())
        for old_name in list(self._history.keys()):
            if old_name not in active_names:
                # 保留已停止容器的历史，但不再更新
                pass

        # WebSocket 推送
        await self._broadcast_ws({
            "type": "metrics_update",
            "overview": overview.to_dict(),
            "containers": {k: v.to_dict() for k, v in new_metrics.items()},
            "active_alerts": [a.to_dict() for a in self._active_alerts.values()],
        })

    async def _list_sandbox_containers(self) -> List[Dict[str, str]]:
        """列出所有 manus-sandbox 容器"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "ps", "-a",
                "--filter", "name=manus-sandbox",
                "--format", '{{.ID}}\t{{.Names}}\t{{.Status}}',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error("docker ps 返回错误 (code=%d): %s", proc.returncode, stderr.decode())
                return []
            logger.debug("docker ps 输出: %s", stdout.decode().strip()[:200])
            containers = []
            for line in stdout.decode().strip().split("\n"):
                if not line.strip():
                    continue
                parts = line.split("\t")
                if len(parts) >= 3:
                    containers.append({
                        "id": parts[0],
                        "name": parts[1],
                        "status": parts[2],
                    })
            return containers
        except Exception as e:
            logger.error("列出容器失败: %s", e)
            return []

    async def _get_container_stats(self, container_id: str) -> Optional[Dict[str, Any]]:
        """获取容器资源统计（docker stats --no-stream --format json）"""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker", "stats", "--no-stream", "--format",
                '{"cpu":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}","mem_perc":"{{.MemPerc}}","net":"{{.NetIO}}","block":"{{.BlockIO}}","pids":"{{.PIDs}}"}',
                container_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=10)
            raw = stdout.decode().strip()
            if not raw:
                return None

            data = json.loads(raw)
            result: Dict[str, Any] = {}

            # CPU
            cpu_str = data.get("cpu", "0%").replace("%", "").strip()
            try:
                result["cpu_percent"] = float(cpu_str)
            except ValueError:
                result["cpu_percent"] = 0.0

            # 内存
            mem_usage_str = data.get("mem_usage", "0B / 0B")
            mem_parts = mem_usage_str.split("/")
            result["memory_usage"] = self._parse_bytes(mem_parts[0].strip()) if len(mem_parts) >= 1 else 0
            result["memory_limit"] = self._parse_bytes(mem_parts[1].strip()) if len(mem_parts) >= 2 else 0

            # 网络 I/O
            net_str = data.get("net", "0B / 0B")
            net_parts = net_str.split("/")
            result["net_rx_bytes"] = self._parse_bytes(net_parts[0].strip()) if len(net_parts) >= 1 else 0
            result["net_tx_bytes"] = self._parse_bytes(net_parts[1].strip()) if len(net_parts) >= 2 else 0

            # 磁盘 I/O
            block_str = data.get("block", "0B / 0B")
            block_parts = block_str.split("/")
            result["disk_read_bytes"] = self._parse_bytes(block_parts[0].strip()) if len(block_parts) >= 1 else 0
            result["disk_write_bytes"] = self._parse_bytes(block_parts[1].strip()) if len(block_parts) >= 2 else 0

            # PID
            pids_str = data.get("pids", "0")
            try:
                result["pids"] = int(pids_str)
            except ValueError:
                result["pids"] = 0

            return result
        except asyncio.TimeoutError:
            logger.warning("获取容器 %s 统计超时", container_id)
            return None
        except Exception as e:
            logger.error("获取容器 %s 统计失败: %s", container_id, e)
            return None

    @staticmethod
    def _parse_bytes(s: str) -> int:
        """解析 Docker 输出的字节数（如 '1.5GiB', '256MiB', '1.2kB'）"""
        s = s.strip()
        if not s:
            return 0
        multipliers = {
            "B": 1, "kB": 1000, "KB": 1024,
            "MB": 1024**2, "MiB": 1024**2,
            "GB": 1024**3, "GiB": 1024**3,
            "TB": 1024**4, "TiB": 1024**4,
        }
        for suffix, mult in sorted(multipliers.items(), key=lambda x: -len(x[0])):
            if s.endswith(suffix):
                try:
                    return int(float(s[:-len(suffix)].strip()) * mult)
                except ValueError:
                    return 0
        try:
            return int(float(s))
        except ValueError:
            return 0

    # ----- 告警引擎 -----

    def _check_alerts(self, metrics: ContainerMetrics):
        """检查指标是否触发告警"""
        now = time.time()

        for metric_name, threshold in self._thresholds.items():
            value = getattr(metrics, metric_name, None)
            if value is None:
                continue

            alert_key = f"{metrics.container_name}:{metric_name}"

            # 判断告警级别
            level = None
            threshold_val = 0.0
            if value >= threshold.critical:
                level = AlertLevel.CRITICAL
                threshold_val = threshold.critical
            elif value >= threshold.warning:
                level = AlertLevel.WARNING
                threshold_val = threshold.warning

            if level:
                # 检查冷却
                last_alert_time = self._alert_cooldown.get(alert_key, 0)
                if now - last_alert_time < ALERT_COOLDOWN:
                    # 更新活跃告警的值
                    if alert_key in self._active_alerts:
                        self._active_alerts[alert_key].value = value
                    continue

                alert = AlertEvent(
                    timestamp=now,
                    container_name=metrics.container_name,
                    conversation_id=metrics.conversation_id,
                    metric=metric_name,
                    value=round(value, 2),
                    threshold=threshold_val,
                    level=level.value,
                    message=f"{threshold.description} {value:.1f}{threshold.unit} 超过{level.value}阈值 {threshold_val}{threshold.unit}",
                )
                self._active_alerts[alert_key] = alert
                self._alerts.appendleft(alert)
                self._alert_cooldown[alert_key] = now

                logger.warning(
                    "[告警][%s] 容器 %s: %s",
                    level.value.upper(),
                    metrics.container_name,
                    alert.message,
                )
            else:
                # 告警恢复
                if alert_key in self._active_alerts:
                    old_alert = self._active_alerts.pop(alert_key)
                    old_alert.resolved = True
                    old_alert.resolved_at = now
                    logger.info(
                        "[告警恢复] 容器 %s: %s 已恢复正常 (当前值: %.1f)",
                        metrics.container_name,
                        metric_name,
                        value,
                    )

    # ----- 公开 API -----

    def get_overview(self) -> Dict[str, Any]:
        """获取系统总览"""
        return self._current_overview.to_dict()

    def get_all_containers(self) -> Dict[str, Any]:
        """获取所有容器当前指标"""
        return {k: v.to_dict() for k, v in self._current_metrics.items()}

    def get_container_metrics(self, container_name: str) -> Optional[Dict[str, Any]]:
        """获取指定容器当前指标"""
        m = self._current_metrics.get(container_name)
        return m.to_dict() if m else None

    def get_container_history(self, container_name: str, duration: int = 300) -> List[Dict[str, Any]]:
        """获取指定容器的历史数据"""
        history = self._history.get(container_name, deque())
        cutoff = time.time() - duration
        return [m.to_dict() for m in history if m.timestamp >= cutoff]

    def get_overview_history(self, duration: int = 300) -> List[Dict[str, Any]]:
        """获取系统总览历史"""
        cutoff = time.time() - duration
        return [o.to_dict() for o in self._overview_history if o.timestamp >= cutoff]

    def get_alerts(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取告警历史"""
        return [a.to_dict() for a in list(self._alerts)[:limit]]

    def get_active_alerts(self) -> List[Dict[str, Any]]:
        """获取当前活跃告警"""
        return [a.to_dict() for a in self._active_alerts.values()]

    def get_thresholds(self) -> Dict[str, Dict[str, Any]]:
        """获取当前告警阈值配置"""
        return {k: asdict(v) for k, v in self._thresholds.items()}

    def update_threshold(self, metric: str, warning: Optional[float] = None, critical: Optional[float] = None):
        """更新告警阈值"""
        if metric not in self._thresholds:
            return False
        t = self._thresholds[metric]
        if warning is not None:
            t.warning = warning
        if critical is not None:
            t.critical = critical
        logger.info("告警阈值已更新: %s -> warning=%.1f, critical=%.1f", metric, t.warning, t.critical)
        return True

    def clear_alerts(self):
        """清除所有告警"""
        self._alerts.clear()
        self._active_alerts.clear()
        self._alert_cooldown.clear()
        logger.info("所有告警已清除")


# ---------------------------------------------------------------------------
# 全局单例
# ---------------------------------------------------------------------------

sandbox_monitor = SandboxMonitor()
