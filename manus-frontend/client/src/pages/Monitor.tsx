/**
 * Monitor - Docker 沙箱监控仪表盘
 * 
 * 功能：
 * 1. 系统总览卡片（容器数、CPU、内存、网络、告警数）
 * 2. 容器列表及实时资源使用
 * 3. 历史趋势图（CPU、内存、网络）
 * 4. 告警面板（活跃告警 + 历史告警）
 * 5. 告警阈值配置
 * 6. WebSocket 实时更新
 */
import { useState, useEffect, useRef, useCallback } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Activity, AlertTriangle, ArrowLeft, Bell, BellOff, Box, Cpu, HardDrive,
  MemoryStick, Network, RefreshCw, Server, Settings, Trash2, Wifi, XCircle,
  CheckCircle2, Clock, ChevronDown, ChevronUp, Pause, Play
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { Badge } from "@/components/ui/badge";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Progress } from "@/components/ui/progress";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { toast } from "sonner";
import { Link } from "wouter";

// ============ Types ============

interface ContainerMetrics {
  timestamp: number;
  container_id: string;
  container_name: string;
  conversation_id: string;
  status: string;
  cpu_percent: number;
  memory_usage_bytes: number;
  memory_limit_bytes: number;
  memory_percent: number;
  memory_usage_mb: number;
  net_rx_bytes: number;
  net_tx_bytes: number;
  net_rx_rate: number;
  net_tx_rate: number;
  disk_read_bytes: number;
  disk_write_bytes: number;
  disk_read_rate: number;
  disk_write_rate: number;
  pids: number;
}

interface SystemOverview {
  timestamp: number;
  timestamp_str?: string;
  total_containers: number;
  running_containers: number;
  stopped_containers: number;
  total_cpu_percent: number;
  total_memory_mb: number;
  total_memory_limit_mb: number;
  total_net_rx_rate: number;
  total_net_tx_rate: number;
  active_alerts: number;
  uptime_seconds: number;
}

interface AlertEvent {
  timestamp: number;
  timestamp_str?: string;
  container_name: string;
  conversation_id: string;
  metric: string;
  value: number;
  threshold: number;
  level: "info" | "warning" | "critical";
  message: string;
  resolved: boolean;
  resolved_at?: number;
  resolved_at_str?: string;
}

interface AlertThreshold {
  metric: string;
  warning: number;
  critical: number;
  unit: string;
  description: string;
}

// ============ Helpers ============

function formatBytes(bytes: number): string {
  if (bytes === 0) return "0 B";
  const k = 1024;
  const sizes = ["B", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(Math.abs(bytes)) / Math.log(k));
  return `${(bytes / Math.pow(k, i)).toFixed(1)} ${sizes[i]}`;
}

function formatRate(bytesPerSec: number): string {
  if (bytesPerSec === 0) return "0 B/s";
  return `${formatBytes(bytesPerSec)}/s`;
}

function formatUptime(seconds: number): string {
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return `${h}h ${m}m ${s}s`;
  if (m > 0) return `${m}m ${s}s`;
  return `${s}s`;
}

function getStatusColor(status: string): string {
  const s = status.toLowerCase();
  if (s.includes("running") || s.includes("up")) return "text-emerald-400";
  if (s.includes("exited")) return "text-amber-400";
  return "text-gray-400";
}

function getAlertLevelColor(level: string): string {
  switch (level) {
    case "critical": return "bg-red-500/20 text-red-400 border-red-500/30";
    case "warning": return "bg-amber-500/20 text-amber-400 border-amber-500/30";
    default: return "bg-blue-500/20 text-blue-400 border-blue-500/30";
  }
}

function getAlertLevelIcon(level: string) {
  switch (level) {
    case "critical": return <XCircle className="w-4 h-4 text-red-400" />;
    case "warning": return <AlertTriangle className="w-4 h-4 text-amber-400" />;
    default: return <Bell className="w-4 h-4 text-blue-400" />;
  }
}

// ============ Mini Sparkline Chart ============

function Sparkline({ data, color = "#818cf8", height = 40, width = 120 }: {
  data: number[];
  color?: string;
  height?: number;
  width?: number;
}) {
  if (data.length < 2) return <div style={{ width, height }} />;
  const max = Math.max(...data, 1);
  const min = Math.min(...data, 0);
  const range = max - min || 1;
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width;
    const y = height - ((v - min) / range) * (height - 4) - 2;
    return `${x},${y}`;
  }).join(" ");

  const areaPoints = `0,${height} ${points} ${width},${height}`;

  return (
    <svg width={width} height={height} className="overflow-visible">
      <defs>
        <linearGradient id={`grad-${color.replace("#", "")}`} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity="0.3" />
          <stop offset="100%" stopColor={color} stopOpacity="0.02" />
        </linearGradient>
      </defs>
      <polygon points={areaPoints} fill={`url(#grad-${color.replace("#", "")})`} />
      <polyline points={points} fill="none" stroke={color} strokeWidth="1.5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

// ============ Overview Cards ============

function OverviewCards({ overview }: { overview: SystemOverview }) {
  const cards = [
    {
      title: "容器总数",
      value: overview.total_containers,
      sub: `${overview.running_containers} 运行 / ${overview.stopped_containers} 停止`,
      icon: <Server className="w-5 h-5" />,
      color: "text-blue-400",
      bgColor: "bg-blue-500/10",
    },
    {
      title: "总 CPU",
      value: `${overview.total_cpu_percent.toFixed(1)}%`,
      sub: `${overview.running_containers} 个活跃容器`,
      icon: <Cpu className="w-5 h-5" />,
      color: "text-emerald-400",
      bgColor: "bg-emerald-500/10",
    },
    {
      title: "总内存",
      value: `${overview.total_memory_mb.toFixed(1)} MB`,
      sub: overview.total_memory_limit_mb > 0
        ? `限制 ${overview.total_memory_limit_mb.toFixed(0)} MB`
        : "无限制",
      icon: <MemoryStick className="w-5 h-5" />,
      color: "text-purple-400",
      bgColor: "bg-purple-500/10",
    },
    {
      title: "网络 I/O",
      value: formatRate(overview.total_net_rx_rate + overview.total_net_tx_rate),
      sub: `↓${formatRate(overview.total_net_rx_rate)} ↑${formatRate(overview.total_net_tx_rate)}`,
      icon: <Network className="w-5 h-5" />,
      color: "text-cyan-400",
      bgColor: "bg-cyan-500/10",
    },
    {
      title: "活跃告警",
      value: overview.active_alerts,
      sub: overview.active_alerts > 0 ? "需要关注" : "一切正常",
      icon: overview.active_alerts > 0
        ? <AlertTriangle className="w-5 h-5" />
        : <CheckCircle2 className="w-5 h-5" />,
      color: overview.active_alerts > 0 ? "text-red-400" : "text-emerald-400",
      bgColor: overview.active_alerts > 0 ? "bg-red-500/10" : "bg-emerald-500/10",
    },
    {
      title: "运行时间",
      value: formatUptime(overview.uptime_seconds),
      sub: "监控引擎",
      icon: <Clock className="w-5 h-5" />,
      color: "text-amber-400",
      bgColor: "bg-amber-500/10",
    },
  ];

  return (
    <div className="grid grid-cols-2 md:grid-cols-3 lg:grid-cols-6 gap-3">
      {cards.map((card, i) => (
        <motion.div
          key={card.title}
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: i * 0.05 }}
        >
          <Card className="bg-card/60 backdrop-blur-sm border-border/50 hover:border-border transition-colors">
            <CardContent className="p-4">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs text-muted-foreground">{card.title}</span>
                <div className={`p-1.5 rounded-md ${card.bgColor} ${card.color}`}>
                  {card.icon}
                </div>
              </div>
              <div className={`text-xl font-bold ${card.color}`}>{card.value}</div>
              <div className="text-xs text-muted-foreground mt-1">{card.sub}</div>
            </CardContent>
          </Card>
        </motion.div>
      ))}
    </div>
  );
}

// ============ Container Card ============

function ContainerCard({ metrics, history }: {
  metrics: ContainerMetrics;
  history: ContainerMetrics[];
}) {
  const [expanded, setExpanded] = useState(false);
  const statusLower = metrics.status.toLowerCase();
  const isRunning = statusLower.includes("running") || statusLower.includes("up");

  const cpuHistory = history.map(h => h.cpu_percent);
  const memHistory = history.map(h => h.memory_percent);

  return (
    <motion.div layout>
      <Card className={`bg-card/60 backdrop-blur-sm border-border/50 hover:border-border/80 transition-all ${
        !isRunning ? "opacity-60" : ""
      }`}>
        <CardContent className="p-4">
          {/* Header */}
          <div className="flex items-center justify-between mb-3">
            <div className="flex items-center gap-2">
              <div className={`w-2 h-2 rounded-full ${isRunning ? "bg-emerald-400 animate-pulse" : "bg-gray-500"}`} />
              <span className="font-mono text-sm font-medium truncate max-w-[200px]">
                {metrics.conversation_id}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <Badge variant="outline" className={`text-xs ${getStatusColor(metrics.status)}`}>
                {isRunning ? "运行中" : "已停止"}
              </Badge>
              <Button
                variant="ghost"
                size="sm"
                className="h-6 w-6 p-0"
                onClick={() => setExpanded(!expanded)}
              >
                {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
              </Button>
            </div>
          </div>

          {isRunning && (
            <>
              {/* CPU & Memory Bars */}
              <div className="space-y-2">
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-muted-foreground flex items-center gap-1">
                      <Cpu className="w-3 h-3" /> CPU
                    </span>
                    <span className="font-mono">{metrics.cpu_percent.toFixed(1)}%</span>
                  </div>
                  <Progress
                    value={Math.min(metrics.cpu_percent, 100)}
                    className="h-1.5"
                  />
                </div>
                <div>
                  <div className="flex items-center justify-between text-xs mb-1">
                    <span className="text-muted-foreground flex items-center gap-1">
                      <MemoryStick className="w-3 h-3" /> 内存
                    </span>
                    <span className="font-mono">
                      {metrics.memory_usage_mb.toFixed(1)} MB
                      {metrics.memory_percent > 0 && ` (${metrics.memory_percent.toFixed(1)}%)`}
                    </span>
                  </div>
                  <Progress
                    value={Math.min(metrics.memory_percent, 100)}
                    className="h-1.5"
                  />
                </div>
              </div>

              {/* Sparklines */}
              <div className="flex items-center gap-4 mt-3">
                <div className="flex-1">
                  <div className="text-[10px] text-muted-foreground mb-1">CPU 趋势</div>
                  <Sparkline data={cpuHistory.slice(-30)} color="#34d399" width={140} height={30} />
                </div>
                <div className="flex-1">
                  <div className="text-[10px] text-muted-foreground mb-1">内存趋势</div>
                  <Sparkline data={memHistory.slice(-30)} color="#a78bfa" width={140} height={30} />
                </div>
              </div>

              {/* Expanded Details */}
              <AnimatePresence>
                {expanded && (
                  <motion.div
                    initial={{ height: 0, opacity: 0 }}
                    animate={{ height: "auto", opacity: 1 }}
                    exit={{ height: 0, opacity: 0 }}
                    className="overflow-hidden"
                  >
                    <div className="mt-3 pt-3 border-t border-border/30 grid grid-cols-2 gap-2 text-xs">
                      <div>
                        <span className="text-muted-foreground">进程数</span>
                        <div className="font-mono">{metrics.pids}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">容器 ID</span>
                        <div className="font-mono truncate">{metrics.container_id.slice(0, 12)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">网络 ↓</span>
                        <div className="font-mono">{formatRate(metrics.net_rx_rate)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">网络 ↑</span>
                        <div className="font-mono">{formatRate(metrics.net_tx_rate)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">磁盘读取</span>
                        <div className="font-mono">{formatRate(metrics.disk_read_rate)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">磁盘写入</span>
                        <div className="font-mono">{formatRate(metrics.disk_write_rate)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">累计接收</span>
                        <div className="font-mono">{formatBytes(metrics.net_rx_bytes)}</div>
                      </div>
                      <div>
                        <span className="text-muted-foreground">累计发送</span>
                        <div className="font-mono">{formatBytes(metrics.net_tx_bytes)}</div>
                      </div>
                    </div>
                  </motion.div>
                )}
              </AnimatePresence>
            </>
          )}
        </CardContent>
      </Card>
    </motion.div>
  );
}

// ============ Alert Panel ============

function AlertPanel({ activeAlerts, alertHistory, onClear }: {
  activeAlerts: AlertEvent[];
  alertHistory: AlertEvent[];
  onClear: () => void;
}) {
  const [tab, setTab] = useState<"active" | "history">("active");

  return (
    <Card className="bg-card/60 backdrop-blur-sm border-border/50">
      <CardHeader className="pb-2">
        <div className="flex items-center justify-between">
          <CardTitle className="text-sm flex items-center gap-2">
            <Bell className="w-4 h-4" />
            告警中心
            {activeAlerts.length > 0 && (
              <Badge variant="destructive" className="text-xs px-1.5 py-0">
                {activeAlerts.length}
              </Badge>
            )}
          </CardTitle>
          <Button variant="ghost" size="sm" className="h-7 text-xs" onClick={onClear}>
            <Trash2 className="w-3 h-3 mr-1" /> 清除
          </Button>
        </div>
      </CardHeader>
      <CardContent className="pt-0">
        <Tabs value={tab} onValueChange={(v) => setTab(v as "active" | "history")}>
          <TabsList className="w-full h-8 mb-2">
            <TabsTrigger value="active" className="text-xs flex-1">
              活跃告警 ({activeAlerts.length})
            </TabsTrigger>
            <TabsTrigger value="history" className="text-xs flex-1">
              历史记录 ({alertHistory.length})
            </TabsTrigger>
          </TabsList>

          <TabsContent value="active" className="mt-0">
            <div className="space-y-2 max-h-[300px] overflow-y-auto pr-1">
              {activeAlerts.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  <CheckCircle2 className="w-8 h-8 mx-auto mb-2 text-emerald-400/50" />
                  暂无活跃告警
                </div>
              ) : (
                activeAlerts.map((alert, i) => (
                  <motion.div
                    key={`${alert.container_name}-${alert.metric}-${alert.timestamp}`}
                    initial={{ opacity: 0, x: -10 }}
                    animate={{ opacity: 1, x: 0 }}
                    transition={{ delay: i * 0.05 }}
                    className={`p-2.5 rounded-lg border ${getAlertLevelColor(alert.level)}`}
                  >
                    <div className="flex items-start gap-2">
                      {getAlertLevelIcon(alert.level)}
                      <div className="flex-1 min-w-0">
                        <div className="text-xs font-medium truncate">{alert.message}</div>
                        <div className="text-[10px] opacity-70 mt-0.5">
                          {alert.container_name} · {alert.timestamp_str}
                        </div>
                      </div>
                      <Badge variant="outline" className="text-[10px] shrink-0">
                        {alert.level}
                      </Badge>
                    </div>
                  </motion.div>
                ))
              )}
            </div>
          </TabsContent>

          <TabsContent value="history" className="mt-0">
            <div className="space-y-1.5 max-h-[300px] overflow-y-auto pr-1">
              {alertHistory.length === 0 ? (
                <div className="text-center py-8 text-muted-foreground text-sm">
                  暂无告警记录
                </div>
              ) : (
                alertHistory.map((alert, i) => (
                  <div
                    key={`hist-${i}`}
                    className={`p-2 rounded-md text-xs ${
                      alert.resolved ? "bg-muted/30 opacity-60" : getAlertLevelColor(alert.level)
                    }`}
                  >
                    <div className="flex items-center gap-1.5">
                      {alert.resolved
                        ? <CheckCircle2 className="w-3 h-3 text-emerald-400" />
                        : getAlertLevelIcon(alert.level)}
                      <span className="truncate flex-1">{alert.message}</span>
                      <span className="text-[10px] opacity-50 shrink-0">{alert.timestamp_str}</span>
                    </div>
                  </div>
                ))
              )}
            </div>
          </TabsContent>
        </Tabs>
      </CardContent>
    </Card>
  );
}

// ============ System Trend Chart ============

function SystemTrendChart({ overviewHistory }: { overviewHistory: SystemOverview[] }) {
  const cpuData = overviewHistory.map(o => o.total_cpu_percent);
  const memData = overviewHistory.map(o => o.total_memory_mb);
  const netData = overviewHistory.map(o => (o.total_net_rx_rate + o.total_net_tx_rate) / 1024);

  return (
    <Card className="bg-card/60 backdrop-blur-sm border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Activity className="w-4 h-4" />
          系统趋势
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="grid grid-cols-3 gap-4">
          <div>
            <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
              <Cpu className="w-3 h-3 text-emerald-400" /> 总 CPU (%)
            </div>
            <Sparkline data={cpuData.slice(-60)} color="#34d399" width={200} height={50} />
            <div className="text-xs font-mono mt-1 text-emerald-400">
              {cpuData.length > 0 ? `${cpuData[cpuData.length - 1]?.toFixed(1)}%` : "N/A"}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
              <MemoryStick className="w-3 h-3 text-purple-400" /> 总内存 (MB)
            </div>
            <Sparkline data={memData.slice(-60)} color="#a78bfa" width={200} height={50} />
            <div className="text-xs font-mono mt-1 text-purple-400">
              {memData.length > 0 ? `${memData[memData.length - 1]?.toFixed(1)} MB` : "N/A"}
            </div>
          </div>
          <div>
            <div className="text-xs text-muted-foreground mb-2 flex items-center gap-1">
              <Network className="w-3 h-3 text-cyan-400" /> 网络 (KB/s)
            </div>
            <Sparkline data={netData.slice(-60)} color="#22d3ee" width={200} height={50} />
            <div className="text-xs font-mono mt-1 text-cyan-400">
              {netData.length > 0 ? `${netData[netData.length - 1]?.toFixed(1)} KB/s` : "N/A"}
            </div>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}

// ============ Threshold Settings ============

function ThresholdSettings({ thresholds, onUpdate }: {
  thresholds: Record<string, AlertThreshold>;
  onUpdate: (metric: string, warning: number, critical: number) => void;
}) {
  const [editing, setEditing] = useState<string | null>(null);
  const [values, setValues] = useState<{ warning: string; critical: string }>({ warning: "", critical: "" });

  const startEdit = (metric: string, t: AlertThreshold) => {
    setEditing(metric);
    setValues({ warning: String(t.warning), critical: String(t.critical) });
  };

  const save = () => {
    if (!editing) return;
    const w = parseFloat(values.warning);
    const c = parseFloat(values.critical);
    if (isNaN(w) || isNaN(c)) {
      toast.error("请输入有效数字");
      return;
    }
    onUpdate(editing, w, c);
    setEditing(null);
  };

  return (
    <Card className="bg-card/60 backdrop-blur-sm border-border/50">
      <CardHeader className="pb-2">
        <CardTitle className="text-sm flex items-center gap-2">
          <Settings className="w-4 h-4" />
          告警阈值配置
        </CardTitle>
      </CardHeader>
      <CardContent>
        <div className="space-y-2">
          {Object.entries(thresholds).map(([metric, t]) => (
            <div key={metric} className="flex items-center justify-between p-2 rounded-md bg-muted/20">
              <div className="text-xs">
                <div className="font-medium">{t.description}</div>
                <div className="text-muted-foreground">{metric}</div>
              </div>
              {editing === metric ? (
                <div className="flex items-center gap-2">
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] text-amber-400">W:</span>
                    <input
                      type="number"
                      value={values.warning}
                      onChange={(e) => setValues(v => ({ ...v, warning: e.target.value }))}
                      className="w-16 h-6 text-xs bg-muted/50 rounded px-1 border border-border/50 focus:outline-none focus:border-primary"
                    />
                  </div>
                  <div className="flex items-center gap-1">
                    <span className="text-[10px] text-red-400">C:</span>
                    <input
                      type="number"
                      value={values.critical}
                      onChange={(e) => setValues(v => ({ ...v, critical: e.target.value }))}
                      className="w-16 h-6 text-xs bg-muted/50 rounded px-1 border border-border/50 focus:outline-none focus:border-primary"
                    />
                  </div>
                  <Button size="sm" className="h-6 text-xs px-2" onClick={save}>保存</Button>
                  <Button size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={() => setEditing(null)}>取消</Button>
                </div>
              ) : (
                <div className="flex items-center gap-3">
                  <div className="text-xs">
                    <span className="text-amber-400">W: {t.warning}{t.unit}</span>
                    <span className="mx-1 text-muted-foreground">/</span>
                    <span className="text-red-400">C: {t.critical}{t.unit}</span>
                  </div>
                  <Button size="sm" variant="ghost" className="h-6 text-xs px-2" onClick={() => startEdit(metric, t)}>
                    编辑
                  </Button>
                </div>
              )}
            </div>
          ))}
        </div>
      </CardContent>
    </Card>
  );
}

// ============ Main Monitor Page ============

export default function Monitor() {
  const [overview, setOverview] = useState<SystemOverview>({
    timestamp: 0, total_containers: 0, running_containers: 0, stopped_containers: 0,
    total_cpu_percent: 0, total_memory_mb: 0, total_memory_limit_mb: 0,
    total_net_rx_rate: 0, total_net_tx_rate: 0, active_alerts: 0, uptime_seconds: 0,
  });
  const [containers, setContainers] = useState<Record<string, ContainerMetrics>>({});
  const [containerHistory, setContainerHistory] = useState<Record<string, ContainerMetrics[]>>({});
  const [overviewHistory, setOverviewHistory] = useState<SystemOverview[]>([]);
  const [activeAlerts, setActiveAlerts] = useState<AlertEvent[]>([]);
  const [alertHistory, setAlertHistory] = useState<AlertEvent[]>([]);
  const [thresholds, setThresholds] = useState<Record<string, AlertThreshold>>({});
  const [connected, setConnected] = useState(false);
  const [paused, setPaused] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const pausedRef = useRef(false);

  // Fetch initial data
  const fetchData = useCallback(async () => {
    try {
      const [ovRes, contRes, alertRes, activeRes, threshRes, histRes] = await Promise.all([
        fetch("/api/monitor/overview"),
        fetch("/api/monitor/containers"),
        fetch("/api/monitor/alerts?limit=100"),
        fetch("/api/monitor/alerts/active"),
        fetch("/api/monitor/thresholds"),
        fetch("/api/monitor/overview/history?duration=600"),
      ]);

      const ovData = await ovRes.json();
      const contData = await contRes.json();
      const alertData = await alertRes.json();
      const activeData = await activeRes.json();
      const threshData = await threshRes.json();
      const histData = await histRes.json();

      if (ovData.success) setOverview(ovData.data);
      if (contData.success) setContainers(contData.data);
      if (alertData.success) setAlertHistory(alertData.data);
      if (activeData.success) setActiveAlerts(activeData.data);
      if (threshData.success) setThresholds(threshData.data);
      if (histData.success) setOverviewHistory(histData.data);
    } catch (err) {
      console.error("获取监控数据失败:", err);
    }
  }, []);

  // WebSocket connection
  useEffect(() => {
    fetchData();

    const protocol = window.location.protocol === "https:" ? "wss:" : "ws:";
    const wsUrl = `${protocol}//${window.location.host}/ws/monitor`;

    const connect = () => {
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        console.log("监控 WebSocket 已连接");
      };

      ws.onmessage = (event) => {
        if (pausedRef.current) return;
        try {
          const data = JSON.parse(event.data);
          if (data.type === "metrics_update") {
            setOverview(data.overview);
            setContainers(data.containers);
            setActiveAlerts(data.active_alerts || []);

            // 追加历史
            setOverviewHistory(prev => {
              const next = [...prev, data.overview];
              return next.slice(-720); // 保留最近 720 个点
            });

            // 追加容器历史
            setContainerHistory(prev => {
              const next = { ...prev };
              for (const [name, metrics] of Object.entries(data.containers)) {
                if (!next[name]) next[name] = [];
                next[name] = [...next[name], metrics as ContainerMetrics].slice(-120);
              }
              return next;
            });
          }
        } catch (err) {
          console.error("解析 WebSocket 数据失败:", err);
        }
      };

      ws.onclose = () => {
        setConnected(false);
        console.log("监控 WebSocket 已断开，5s 后重连...");
        setTimeout(connect, 5000);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    connect();

    // 定期刷新告警历史
    const alertInterval = setInterval(async () => {
      try {
        const res = await fetch("/api/monitor/alerts?limit=100");
        const data = await res.json();
        if (data.success) setAlertHistory(data.data);
      } catch { /* ignore */ }
    }, 15000);

    return () => {
      clearInterval(alertInterval);
      if (wsRef.current) {
        wsRef.current.close();
      }
    };
  }, [fetchData]);

  // Sync paused ref
  useEffect(() => {
    pausedRef.current = paused;
  }, [paused]);

  const handleClearAlerts = async () => {
    try {
      await fetch("/api/monitor/alerts/clear", { method: "POST" });
      setActiveAlerts([]);
      setAlertHistory([]);
      toast.success("告警已清除");
    } catch {
      toast.error("清除告警失败");
    }
  };

  const handleUpdateThreshold = async (metric: string, warning: number, critical: number) => {
    try {
      const res = await fetch(`/api/monitor/thresholds/${metric}?warning=${warning}&critical=${critical}`, {
        method: "PUT",
      });
      const data = await res.json();
      if (data.success) {
        setThresholds(prev => ({ ...prev, [metric]: data.data }));
        toast.success("阈值已更新");
      }
    } catch {
      toast.error("更新阈值失败");
    }
  };

  const containerList = Object.values(containers).sort((a, b) => {
    const aRunning = (a.status.toLowerCase().includes("running") || a.status.toLowerCase().includes("up")) ? 1 : 0;
    const bRunning = (b.status.toLowerCase().includes("running") || b.status.toLowerCase().includes("up")) ? 1 : 0;
    return bRunning - aRunning;
  });

  return (
    <div className="min-h-screen bg-background text-foreground">
      {/* Header */}
      <header className="sticky top-0 z-50 bg-background/80 backdrop-blur-md border-b border-border/50">
        <div className="max-w-[1600px] mx-auto px-4 h-14 flex items-center justify-between">
          <div className="flex items-center gap-3">
            <Link href="/">
              <Button variant="ghost" size="sm" className="h-8 gap-1">
                <ArrowLeft className="w-4 h-4" />
                返回
              </Button>
            </Link>
            <div className="flex items-center gap-2">
              <Activity className="w-5 h-5 text-primary" />
              <h1 className="text-lg font-bold">沙箱监控仪表盘</h1>
            </div>
          </div>
          <div className="flex items-center gap-2">
            <Tooltip>
              <TooltipTrigger asChild>
                <div className={`flex items-center gap-1.5 px-2 py-1 rounded-full text-xs ${
                  connected ? "bg-emerald-500/10 text-emerald-400" : "bg-red-500/10 text-red-400"
                }`}>
                  <Wifi className="w-3 h-3" />
                  {connected ? "已连接" : "断开"}
                </div>
              </TooltipTrigger>
              <TooltipContent>WebSocket 实时连接状态</TooltipContent>
            </Tooltip>
            <Button
              variant="ghost"
              size="sm"
              className="h-8"
              onClick={() => setPaused(!paused)}
            >
              {paused ? <Play className="w-4 h-4" /> : <Pause className="w-4 h-4" />}
              <span className="ml-1 text-xs">{paused ? "恢复" : "暂停"}</span>
            </Button>
            <Button variant="ghost" size="sm" className="h-8" onClick={fetchData}>
              <RefreshCw className="w-4 h-4" />
              <span className="ml-1 text-xs">刷新</span>
            </Button>
          </div>
        </div>
      </header>

      {/* Content */}
      <main className="max-w-[1600px] mx-auto px-4 py-4 space-y-4">
        {/* Overview Cards */}
        <OverviewCards overview={overview} />

        {/* System Trend */}
        <SystemTrendChart overviewHistory={overviewHistory} />

        {/* Main Grid: Containers + Alerts */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-4">
          {/* Container List */}
          <div className="lg:col-span-2 space-y-3">
            <div className="flex items-center justify-between">
              <h2 className="text-sm font-semibold flex items-center gap-2">
                <Box className="w-4 h-4" />
                容器列表 ({containerList.length})
              </h2>
            </div>
            {containerList.length === 0 ? (
              <Card className="bg-card/60 backdrop-blur-sm border-border/50">
                <CardContent className="py-12 text-center text-muted-foreground">
                  <Server className="w-12 h-12 mx-auto mb-3 opacity-30" />
                  <p className="text-sm">暂无沙箱容器运行</p>
                  <p className="text-xs mt-1">创建新的对话会话后，容器将自动启动</p>
                </CardContent>
              </Card>
            ) : (
              <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                {containerList.map(m => (
                  <ContainerCard
                    key={m.container_name}
                    metrics={m}
                    history={containerHistory[m.container_name] || []}
                  />
                ))}
              </div>
            )}
          </div>

          {/* Right Panel: Alerts + Settings */}
          <div className="space-y-4">
            <AlertPanel
              activeAlerts={activeAlerts}
              alertHistory={alertHistory}
              onClear={handleClearAlerts}
            />
            <ThresholdSettings
              thresholds={thresholds}
              onUpdate={handleUpdateThreshold}
            />
          </div>
        </div>
      </main>
    </div>
  );
}
