/**
 * VncOverlay — 全屏 VNC 远程桌面覆盖层
 * ESC 或底部按钮关闭
 */
import { useState, useEffect, useCallback, useMemo } from "react";
import { motion, AnimatePresence } from "framer-motion";
import { X, Loader2, WifiOff } from "lucide-react";
import VncViewer, { type VncStatus } from "./VncViewer";

const API_TOKEN = (import.meta.env.VITE_MANUS_API_TOKEN || "").trim();

interface VncOverlayProps {
  conversationId?: string;
  open: boolean;
  onClose: () => void;
}

export default function VncOverlay({ conversationId, open, onClose }: VncOverlayProps) {
  const [status, setStatus] = useState<VncStatus>("connecting");

  // 构建 VNC WebSocket URL
  const wsUrl = useMemo(() => {
    if (!conversationId) return "";
    const proto = window.location.protocol === "https:" ? "wss:" : "ws:";
    const host = window.location.host;
    const tokenParam = API_TOKEN ? `?token=${encodeURIComponent(API_TOKEN)}` : "";
    return `${proto}//${host}/ws/vnc/${conversationId}${tokenParam}`;
  }, [conversationId]);

  // ESC 关闭
  useEffect(() => {
    if (!open) return;
    const handleKeyDown = (e: KeyboardEvent) => {
      if (e.key === "Escape") {
        e.preventDefault();
        onClose();
      }
    };
    window.addEventListener("keydown", handleKeyDown);
    return () => window.removeEventListener("keydown", handleKeyDown);
  }, [open, onClose]);

  const handleStatusChange = useCallback((s: VncStatus) => {
    setStatus(s);
  }, []);

  return (
    <AnimatePresence>
      {open && conversationId && (
        <motion.div
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          exit={{ opacity: 0 }}
          transition={{ duration: 0.2 }}
          className="fixed inset-0 z-[100] flex flex-col bg-black/95"
        >
          {/* VNC 画面 */}
          <div className="flex-1 relative">
            {wsUrl && (
              <VncViewer
                url={wsUrl}
                onStatusChange={handleStatusChange}
                className="absolute inset-0"
              />
            )}

            {/* 连接状态提示 */}
            {status === "connecting" && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/60 pointer-events-none">
                <div className="flex items-center gap-2 text-white/80">
                  <Loader2 className="w-5 h-5 animate-spin" />
                  <span className="text-sm">正在连接远程桌面...</span>
                </div>
              </div>
            )}

            {(status === "disconnected" || status === "error") && (
              <div className="absolute inset-0 flex items-center justify-center bg-black/60 pointer-events-none">
                <div className="flex items-center gap-2 text-red-400">
                  <WifiOff className="w-5 h-5" />
                  <span className="text-sm">
                    {status === "error" ? "连接失败" : "连接已断开"}
                  </span>
                </div>
              </div>
            )}
          </div>

          {/* 底部控制栏 */}
          <div className="flex items-center justify-center gap-3 px-4 py-2 bg-black/80 border-t border-white/10">
            <span className="text-xs text-white/50">
              按 ESC 退出远程桌面
            </span>
            <button
              onClick={onClose}
              className="inline-flex items-center gap-1.5 px-3 py-1.5 rounded-md bg-white/10 hover:bg-white/20 text-white/80 text-xs transition-colors"
            >
              <X className="w-3.5 h-3.5" />
              退出
            </button>
          </div>
        </motion.div>
      )}
    </AnimatePresence>
  );
}
