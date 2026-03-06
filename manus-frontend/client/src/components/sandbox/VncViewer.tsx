/**
 * VncViewer — noVNC RFB 封装组件
 * 使用 @novnc/novnc 库创建 RFB 连接到 VNC WebSocket relay
 */
import { useEffect, useRef, useCallback } from "react";
import RFB from "@novnc/novnc/lib/rfb";

export type VncStatus = "connecting" | "connected" | "disconnected" | "error";

interface VncViewerProps {
  url: string;
  onStatusChange?: (status: VncStatus) => void;
  viewOnly?: boolean;
  scaleViewport?: boolean;
  className?: string;
}

export default function VncViewer({
  url,
  onStatusChange,
  viewOnly = false,
  scaleViewport = true,
  className = "",
}: VncViewerProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const rfbRef = useRef<RFB | null>(null);

  const notify = useCallback(
    (status: VncStatus) => onStatusChange?.(status),
    [onStatusChange]
  );

  useEffect(() => {
    const el = containerRef.current;
    if (!el || !url) return;

    notify("connecting");

    const rfb = new RFB(el, url);
    rfb.viewOnly = viewOnly;
    rfb.scaleViewport = scaleViewport;
    rfb.background = "rgb(20, 20, 30)";
    rfbRef.current = rfb;

    rfb.addEventListener("connect", () => notify("connected"));
    rfb.addEventListener("disconnect", () => notify("disconnected"));
    rfb.addEventListener("securityfailure", () => notify("error"));

    return () => {
      try {
        rfb.disconnect();
      } catch {
        // ignore
      }
      rfbRef.current = null;
    };
  }, [url, viewOnly, scaleViewport, notify]);

  return (
    <div
      ref={containerRef}
      className={`w-full h-full ${className}`}
      style={{ overflow: "hidden" }}
    />
  );
}
