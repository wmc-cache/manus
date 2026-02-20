/**
 * TerminalWindow - 终端窗口组件
 * 展示 Agent 在沙箱中执行命令的过程
 * 
 * 风格: 深色终端 + 毛玻璃边框 + 绿色光标
 */
import { useRef, useEffect, useMemo } from "react";
import { Terminal as TerminalIcon } from "lucide-react";

interface TerminalWindowProps {
  output: string;
  onInput?: (data: string) => void;
}

export default function TerminalWindow({ output, onInput }: TerminalWindowProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  const bottomRef = useRef<HTMLDivElement>(null);

  // 自动滚动到底部
  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [output]);

  // 解析输出为带颜色的行
  const lines = useMemo(() => {
    if (!output) return [];
    return output.split("\n").map((line, i) => {
      let className = "text-emerald-300/90";
      if (line.startsWith("$")) {
        className = "text-sky-400 font-semibold";
      } else if (line.includes("[STDERR]") || line.toLowerCase().includes("error")) {
        className = "text-red-400";
      } else if (line.includes("warning") || line.includes("Warning")) {
        className = "text-amber-400";
      } else if (line.startsWith("manus:")) {
        className = "text-emerald-400 font-semibold";
      }
      return { text: line, className, key: i };
    });
  }, [output]);

  return (
    <div className="h-full flex flex-col bg-[oklch(0.1_0.01_260)] rounded-lg overflow-hidden">
      {/* 终端头部 */}
      <div className="flex items-center gap-2 px-4 py-2.5 bg-[oklch(0.13_0.012_260)] border-b border-border/20">
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

      {/* 终端内容 */}
      <div
        ref={containerRef}
        className="flex-1 overflow-y-auto p-4 font-mono text-sm leading-relaxed"
        onClick={() => {
          // 聚焦以接收键盘输入
        }}
      >
        {lines.length === 0 ? (
          <div className="text-muted-foreground/50 flex items-center gap-2">
            <span className="inline-block w-2 h-4 bg-emerald-400/70 animate-pulse" />
            <span>等待 Agent 执行命令...</span>
          </div>
        ) : (
          <>
            {lines.map(({ text, className, key }) => (
              <div key={key} className={`${className} whitespace-pre-wrap break-all`}>
                {text || "\u00A0"}
              </div>
            ))}
            {/* 光标 */}
            <span className="inline-block w-2 h-4 bg-emerald-400/70 animate-pulse ml-0.5" />
          </>
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  );
}
