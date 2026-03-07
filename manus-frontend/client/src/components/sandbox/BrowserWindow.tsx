/**
 * BrowserWindow - 浏览器窗口组件
 * 展示 Agent 浏览网页的实时截图
 * 
 * 风格: Chrome 风格 + 毛玻璃 + 地址栏
 */
import { Globe, RefreshCw, Lock, ExternalLink } from "lucide-react";
import { useRef, useState } from "react";

interface BrowserWindowProps {
  data: {
    url: string;
    title: string;
    screenshot: string; // base64
    status?: number;
  } | null;
  onPageClick?: (x: number, y: number, viewportWidth: number, viewportHeight: number) => void;
  onTypeText?: (text: string, submit?: boolean) => void;
  onNavigate?: (url: string) => void;
  onScrollPage?: (deltaY: number) => void;
  onPressKey?: (key: "Enter" | "Tab" | "Escape") => void;
  interactionError?: string | null;
}

export default function BrowserWindow({
  data,
  onPageClick,
  onTypeText,
  onNavigate,
  onScrollPage,
  onPressKey,
  interactionError,
}: BrowserWindowProps) {
  const screenshotRef = useRef<HTMLImageElement | null>(null);
  const [typingText, setTypingText] = useState("");

  const normalizeManualUrl = (raw: string): string | null => {
    const value = raw.trim();
    if (!value || /\s/.test(value)) return null;

    const hasScheme = /^[a-zA-Z][a-zA-Z\d+\-.]*:\/\//.test(value);
    const looksLikeHost =
      /^localhost(?::\d+)?(?:\/.*)?$/i.test(value) ||
      /^\d{1,3}(?:\.\d{1,3}){3}(?::\d+)?(?:\/.*)?$/.test(value) ||
      /^(?:[\w-]+\.)+[\w-]{2,}(?::\d+)?(?:\/.*)?$/i.test(value);
    if (!hasScheme && !looksLikeHost) return null;

    return hasScheme ? value : `https://${value}`;
  };

  const handlePrimaryAction = () => {
    const text = typingText.trim();
    if (!text) return;

    const normalizedUrl = onNavigate ? normalizeManualUrl(text) : null;
    if (normalizedUrl) {
      onNavigate?.(normalizedUrl);
      setTypingText("");
      return;
    }
    if (!onTypeText) return;
    onTypeText(text, false);
    setTypingText("");
  };

  const hasInput = Boolean(typingText.trim());
  const isUrlInput = Boolean(onNavigate && normalizeManualUrl(typingText));
  const primaryButtonLabel = isUrlInput ? "访问" : "输入";
  const primaryButtonTitle = isUrlInput ? "访问该地址" : "输入到当前焦点元素";

  if (!data) {
    return (
      <div className="h-full flex items-center justify-center bg-[oklch(0.1_0.01_260)] rounded-lg">
        <div className="text-center text-muted-foreground/50">
          <Globe className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">等待 Agent 打开网页...</p>
        </div>
      </div>
    );
  }

  const isLoading = data.title === "加载中...";
  const isHttps = data.url.startsWith("https://");

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
            {data.title || "新标签页"}
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
            {data.url}
          </span>
        </div>

        {data.url && (
          <a
            href={data.url}
            target="_blank"
            rel="noopener noreferrer"
            className="p-1 rounded hover:bg-accent/30 transition-colors"
          >
            <ExternalLink className="w-3.5 h-3.5 text-muted-foreground" />
          </a>
        )}
      </div>

      {/* 交互栏 */}
      {/* <div className="flex items-center gap-2 px-3 py-1.5 bg-[oklch(0.12_0.012_260)] border-b border-border/20">
        <input
          value={typingText}
          onChange={(e) => setTypingText(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === "Enter") {
              e.preventDefault();
              handlePrimaryAction();
            }
          }}
          disabled={!onNavigate && !onTypeText}
          placeholder="输入 URL 访问，或先点页面元素后输入文本"
          className="flex-1 h-7 rounded px-2 text-xs bg-[oklch(0.16_0.014_260)] border border-border/20 outline-none disabled:opacity-60"
        />

        <button
          onClick={handlePrimaryAction}
          disabled={!hasInput || (!onNavigate && !onTypeText)}
          title={primaryButtonTitle}
          className="h-7 px-2 text-[10px] rounded bg-primary/20 text-primary disabled:opacity-40"
        >
          {primaryButtonLabel}
        </button>
        <button
          onClick={() => onPressKey?.("Enter")}
          disabled={!onPressKey}
          className="h-7 px-2 text-[10px] rounded bg-primary/20 text-primary disabled:opacity-40"
        >
          Enter
        </button>
      </div> */}

      {/* 页面截图 */}
      <div
        className="flex-1 overflow-auto bg-white"
        onWheel={(e) => {
          if (!onScrollPage) return;
          e.preventDefault();
          onScrollPage(e.deltaY);
        }}
      >
        {data.screenshot ? (
          <img
            ref={screenshotRef}
            src={`data:image/jpeg;base64,${data.screenshot}`}
            alt={data.title}
            className={`w-full h-auto ${onPageClick ? "cursor-crosshair" : "cursor-default"}`}
            onClick={(e) => {
              if (!onPageClick || !screenshotRef.current) return;
              const rect = screenshotRef.current.getBoundingClientRect();
              const x = e.clientX - rect.left;
              const y = e.clientY - rect.top;
              onPageClick(x, y, rect.width, rect.height);
            }}
          />
        ) : isLoading ? (
          <div className="h-full flex items-center justify-center bg-[oklch(0.1_0.01_260)]">
            <div className="text-center">
              <RefreshCw className="w-8 h-8 mx-auto mb-2 text-primary animate-spin" />
              <p className="text-sm text-muted-foreground">正在加载页面...</p>
            </div>
          </div>
        ) : (
          <div className="h-full flex items-center justify-center bg-[oklch(0.1_0.01_260)]">
            <p className="text-sm text-muted-foreground">无截图</p>
          </div>
        )}
      </div>

      {interactionError && (
        <div className="px-3 py-1.5 text-[10px] text-red-300 bg-red-500/10 border-t border-red-500/20">
          {interactionError}
        </div>
      )}

      {/* 状态栏 */}
      {data.status !== undefined && data.status > 0 && (
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
