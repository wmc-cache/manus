/**
 * TerminalWindow - 真实终端模拟器
 * 基于 xterm.js 实现，支持光标处交互、ANSI 转义序列、Agent 命令实时显示
 *
 * 风格: 深色终端 + 毛玻璃边框 + 绿色光标
 */
import { useRef, useEffect, useCallback } from "react";
import { Terminal as TerminalIcon } from "lucide-react";
import { Terminal } from "@xterm/xterm";
import { FitAddon } from "@xterm/addon-fit";
import { WebLinksAddon } from "@xterm/addon-web-links";
import "@xterm/xterm/css/xterm.css";

interface TerminalWindowProps {
  /** 来自后端 PTY 的原始终端输出（含 ANSI 转义序列） */
  output: string;
  /** 向后端 PTY 发送用户输入 */
  onInput?: (data: string) => void;
  /** 通知后端终端尺寸变化 */
  onResize?: (cols: number, rows: number) => void;
}

export default function TerminalWindow({
  output,
  onInput,
  onResize,
}: TerminalWindowProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const termRef = useRef<Terminal | null>(null);
  const fitAddonRef = useRef<FitAddon | null>(null);
  /** 已写入 xterm 的 output 长度，用于增量写入 */
  const writtenLenRef = useRef(0);
  /** 标记是否已完成首次初始化 */
  const initializedRef = useRef(false);

  // ── 初始化 xterm 实例 ──────────────────────────────────
  useEffect(() => {
    if (!containerRef.current || initializedRef.current) return;

    const term = new Terminal({
      cursorBlink: true,
      cursorStyle: "block",
      fontSize: 13,
      fontFamily: "'JetBrains Mono', 'Fira Code', 'Cascadia Code', Menlo, Monaco, 'Courier New', monospace",
      lineHeight: 1.35,
      scrollback: 5000,
      theme: {
        background: "#0a0e14",
        foreground: "#d4d4d8",
        cursor: "#34d399",
        cursorAccent: "#0a0e14",
        selectionBackground: "#34d39940",
        selectionForeground: "#ffffff",
        black: "#1a1a2e",
        red: "#f87171",
        green: "#34d399",
        yellow: "#fbbf24",
        blue: "#60a5fa",
        magenta: "#c084fc",
        cyan: "#22d3ee",
        white: "#d4d4d8",
        brightBlack: "#4a4a5a",
        brightRed: "#fca5a5",
        brightGreen: "#6ee7b7",
        brightYellow: "#fde68a",
        brightBlue: "#93c5fd",
        brightMagenta: "#d8b4fe",
        brightCyan: "#67e8f9",
        brightWhite: "#fafafa",
      },
      allowProposedApi: true,
      convertEol: false,
    });

    const fitAddon = new FitAddon();
    const webLinksAddon = new WebLinksAddon();

    term.loadAddon(fitAddon);
    term.loadAddon(webLinksAddon);

    term.open(containerRef.current);

    // 首次 fit
    requestAnimationFrame(() => {
      try {
        fitAddon.fit();
      } catch {
        // 容器可能尚未可见
      }
    });

    // 用户在终端中输入时，发送到后端 PTY
    term.onData((data) => {
      onInput?.(data);
    });

    // 终端尺寸变化时通知后端
    term.onResize(({ cols, rows }) => {
      onResize?.(cols, rows);
    });

    termRef.current = term;
    fitAddonRef.current = fitAddon;
    initializedRef.current = true;

    // 监听容器尺寸变化，自动 fit
    const resizeObserver = new ResizeObserver(() => {
      requestAnimationFrame(() => {
        try {
          fitAddon.fit();
        } catch {
          // ignore
        }
      });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      term.dispose();
      termRef.current = null;
      fitAddonRef.current = null;
      initializedRef.current = false;
      writtenLenRef.current = 0;
    };
    // onInput / onResize 通过 ref 捕获，不需要作为依赖
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ── 保持 onInput / onResize 回调最新 ──────────────────
  const onInputRef = useRef(onInput);
  const onResizeRef = useRef(onResize);
  useEffect(() => {
    onInputRef.current = onInput;
    onResizeRef.current = onResize;
  }, [onInput, onResize]);

  // 重新绑定 onData（因为闭包捕获了旧的 onInput）
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;
    const disposable = term.onData((data) => {
      onInputRef.current?.(data);
    });
    return () => disposable.dispose();
  }, []);

  // ── 增量写入 output 到 xterm ──────────────────────────
  useEffect(() => {
    const term = termRef.current;
    if (!term) return;

    if (output.length === 0 && writtenLenRef.current > 0) {
      // output 被清空（新对话），重置终端
      term.clear();
      term.reset();
      writtenLenRef.current = 0;
      return;
    }

    if (output.length > writtenLenRef.current) {
      const newData = output.slice(writtenLenRef.current);
      term.write(newData);
      writtenLenRef.current = output.length;
    }
  }, [output]);

  // ── 手动 fit（当标签切换回终端时可能需要） ────────────
  const handleFocus = useCallback(() => {
    requestAnimationFrame(() => {
      try {
        fitAddonRef.current?.fit();
        termRef.current?.focus();
      } catch {
        // ignore
      }
    });
  }, []);

  return (
    <div
      className="h-full flex flex-col bg-[#0a0e14] rounded-lg overflow-hidden"
      onClick={handleFocus}
    >
      {/* 终端头部 */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-[oklch(0.13_0.012_260)] border-b border-border/20 shrink-0">
        <div className="flex gap-1.5">
          <div className="w-3 h-3 rounded-full bg-red-500/70" />
          <div className="w-3 h-3 rounded-full bg-amber-500/70" />
          <div className="w-3 h-3 rounded-full bg-emerald-500/70" />
        </div>
        <div className="flex items-center gap-1.5 ml-3">
          <TerminalIcon className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground font-mono">
            manus@sandbox:~
          </span>
        </div>
      </div>

      {/* xterm.js 终端容器 */}
      <div
        ref={containerRef}
        className="flex-1 overflow-hidden"
        style={{ padding: "4px 0 0 4px" }}
      />
    </div>
  );
}
