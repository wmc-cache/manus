/**
 * BrowserWindow - 浏览器窗口组件
 * 展示 Agent 浏览器的实时 VNC 画面
 *
 * 风格: Chrome 风格 + 毛玻璃 + 地址栏
 */
import { Globe, RefreshCw, Lock, ExternalLink, Loader2, WifiOff } from "lucide-react";
import { useMemo, useState, useCallback } from "react";
import VncViewer, { type VncStatus } from "./VncViewer";

const API_TOKEN = (import.meta.env.VITE_MANUS_API_TOKEN || "").trim();

interface BrowserWindowProps {
  data: {
    url: string;
    title: string;
    screenshot: string; // base64
    status?: number;
  } | null;
  conversationId?: string;
  onPageClick?: (x: number, y: number, viewportWidth: number, viewportHeight: number) => void;
  onTypeText?: (text: string, submit?: boolean) => void;
  onNavigate?: (url: string) => void;
  onScrollPage?: (deltaY: number) => void;
  onPressKey?: (key: "Enter" | "Tab" | "Escape") => void;
  interactionError?: string | null;
}

export default function BrowserWindow({
  data,
  conversationId,
  interactionError,
}: BrowserWindowProps) {
  const [vncStatus, setVncStatus] = useState<VncStatus>("connecting");

  const wsUrl = useMemo(() => {
    if (!conversationId) return "";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const tokenParam = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : "";
    return `${proto}//${host}/ws/vnc/${conversationId}${tokenParam}`;
  }, [conversationId]);

  const handleVncStatusChange = useCallback((status: VncStatus) => {
    setVncStatus(status);
  }, []);

  const isLoading = data?.title === "加载中...";
  const currentUrl = data?.url || "";
  const currentTitle = data?.title || "浏览器";
  const isHttps = currentUrl.startsWith("https://");

  return (
    <div className="h-full flex flex-col bg-[oklch(0.1_0.01_260)] rounded-lg overflow-hidden">
      {/* 浏览器头部 - 标签栏 */}
      <div className="flex items-center gap-2 px-4 py-2 bg-[oklch(0.13_0.012_260)] border-b border-border/20">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500/70" />
          <div className="w-3 h-3 rounded-full bg-amber-500/70" />
          <div className="w-3 h-3 rounded-full bg-emerald-500/70" />
        </div>

        {/* 标签页 */}
        <div className="flex items-center gap-1 ml-3 px-3 py-1 rounded-t-lg bg-[oklch(0.16_0.014_260)] border border-b-0 border-border/20 max-w-[200px]">
          <Globe className="w-3 h-3 text-muted-foreground flex-shrink-0" />
          <span className="text-xs text-foreground/80 truncate">
            {currentTitle}
          </span>
        </div>
      </div>

      {/* 地址栏 */}
      <div className="flex items-center gap-2 px-3 py-1.5 bg-[oklch(0.12_0.012_260)] border-b border-border/20">
        <button className="p-1 rounded hover:bg-accent/30 transition-colors">
          {isLoading ? (
            <RefreshCw className="w-3.5 h-3.5 text-muted-foreground animate-spin" />
          ) : (
            <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
          )}
        </button>

        <div className="flex-1 flex items-center gap-1.5 px-3 py-1 rounded-md bg-[oklch(0.16_0.014_260)] border border-border/15">
          {isHttps && <Lock className="w-3 h-3 text-emerald-400/70 flex-shrink-0" />}
          <span className="text-xs text-muted-foreground font-mono truncate">
            {currentUrl || "等待 Agent 打开网页..."}
          </span>
        </div>

        {currentUrl && (
          <a
            href={currentUrl}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1 rounded hover:bg-accent/30 transition-colors"
          >
            <ExternalLink className="w-3.5 h-3.5 text-muted-foreground" />
          </a>
        )}
      </div>

      {/* 页面实时画面 */}
      <div className="relative flex-1 overflow-hidden bg-white">
        {wsUrl ? (
          <VncViewer
            url={wsUrl}
            onStatusChange={handleVncStatusChange}
            className="absolute inset-0"
          />
        ) : (
          <div className="h-full flex items-center justify-center bg-[oklch(0.1_0.01_260)]">
            <div className="text-center text-muted-foreground/50">
              <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
              <p className="text-sm">等待对话初始化浏览器...</p>
            </div>
          </div>
        )}

        {vncStatus === "connecting" && wsUrl && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/35 pointer-events-none">
            <div className="flex items-center gap-2 text-white/85">
              <Loader2 className="w-5 h-5 animate-spin" />
              <span className="text-sm">{isLoading ? "正在打开网页..." : "正在连接实时画面..."}</span>
            </div>
          </div>
        )}

        {(vncStatus === "disconnected" || vncStatus === "error") && (
          <div className="absolute inset-0 flex items-center justify-center bg-black/50 pointer-events-none">
            <div className="flex items-center gap-2 text-red-300">
              <WifiOff className="w-5 h-5" />
              <span className="text-sm">{vncStatus === "error" ? "实时画面连接失败" : "实时画面已断开"}</span>
            </div>
          </div>
        )}
      </div>

      {interactionError && (
        <div className="px-3 py-1.5 text-[10px] text-red-300 bg-red-500/10 border-t border-red-500/20">
          {interactionError}
        </div>
      )}

      {/* 状态栏 */}
      {data?.status !== undefined && data.status > 0 && (
        <div className="flex items-center gap-2 px-3 py-1 bg-[oklch(0.12_0.012_260)] border-t border-border/20">
          <div
            className={`w-1.5 h-1.5 rounded-full ${
              data.status >= 200 && data.status < 400
                ? "bg-emerald-400"
                : "bg-red-400"
            }`}
          />
          <span className="text-[10px] text-muted-foreground/60 font-mono">
            HTTP {data.status}
          </span>
        </div>
      )}
    </div>
  );
}
