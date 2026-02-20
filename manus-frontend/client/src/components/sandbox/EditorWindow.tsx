/**
 * EditorWindow - 代码编辑器窗口组件
 * 展示 Agent 正在编辑的文件内容
 * 
 * 风格: VS Code 风格 + 毛玻璃 + 语法高亮
 */
import React, { useMemo } from "react";
import { FileCode, Copy, Check } from "lucide-react";
import { useState } from "react";

interface EditorWindowProps {
  file: {
    path: string;
    name: string;
    content: string;
    language: string;
  } | null;
}

// 简单的语法高亮（关键词着色）
function highlightLine(line: string, language: string): React.JSX.Element {
  if (!line) return <span>{"\u00A0"}</span>;

  const pythonKeywords = /\b(def|class|import|from|return|if|elif|else|for|while|try|except|finally|with|as|yield|lambda|async|await|raise|pass|break|continue|and|or|not|in|is|True|False|None|self)\b/g;
  const jsKeywords = /\b(const|let|var|function|return|if|else|for|while|try|catch|finally|class|import|export|from|async|await|new|this|throw|typeof|instanceof|true|false|null|undefined|switch|case|break|default)\b/g;
  const stringPattern = /(["'`])(?:(?=(\\?))\2.)*?\1/g;
  const commentPattern = /(\/\/.*$|#.*$)/gm;
  const numberPattern = /\b(\d+\.?\d*)\b/g;

  let keywords: RegExp;
  switch (language) {
    case "python":
      keywords = pythonKeywords;
      break;
    case "javascript":
    case "typescript":
    case "typescriptreact":
    case "javascriptreact":
      keywords = jsKeywords;
      break;
    default:
      keywords = jsKeywords;
  }

  // 简单替换方式
  const parts: { text: string; type: string }[] = [];
  let remaining = line;
  let lastIndex = 0;

  // 先检测注释
  const commentMatch = remaining.match(commentPattern);
  if (commentMatch) {
    const idx = remaining.indexOf(commentMatch[0]);
    if (idx >= 0) {
      parts.push({ text: remaining.slice(0, idx), type: "code" });
      parts.push({ text: commentMatch[0], type: "comment" });
      remaining = "";
    }
  }

  if (remaining) {
    parts.push({ text: remaining, type: "code" });
  }

  return (
    <span>
      {parts.map((part, i) => {
        if (part.type === "comment") {
          return (
            <span key={i} className="text-emerald-600/80 italic">
              {part.text}
            </span>
          );
        }

        // 对 code 部分做关键词高亮
        const segments: React.JSX.Element[] = [];
        let text = part.text;
        let segKey = 0;

        // 简单的逐词处理
        const tokens = text.split(/(\s+|[{}()[\];,.<>=!+\-*/&|^~?:])/);
        for (const token of tokens) {
          if (keywords.test(token)) {
            keywords.lastIndex = 0;
            segments.push(
              <span key={segKey++} className="text-violet-400 font-medium">
                {token}
              </span>
            );
          } else if (stringPattern.test(token)) {
            stringPattern.lastIndex = 0;
            segments.push(
              <span key={segKey++} className="text-amber-300">
                {token}
              </span>
            );
          } else if (numberPattern.test(token)) {
            numberPattern.lastIndex = 0;
            segments.push(
              <span key={segKey++} className="text-orange-400">
                {token}
              </span>
            );
          } else {
            segments.push(<span key={segKey++}>{token}</span>);
          }
        }

        return <span key={i}>{segments}</span>;
      })}
    </span>
  );
}

export default function EditorWindow({ file }: EditorWindowProps) {
  const [copied, setCopied] = useState(false);

  const lines = useMemo(() => {
    if (!file?.content) return [];
    return file.content.split("\n");
  }, [file?.content]);

  const handleCopy = async () => {
    if (file?.content) {
      await navigator.clipboard.writeText(file.content);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  };

  if (!file) {
    return (
      <div className="h-full flex items-center justify-center bg-[oklch(0.1_0.01_260)] rounded-lg">
        <div className="text-center text-muted-foreground/50">
          <FileCode className="w-12 h-12 mx-auto mb-3 opacity-30" />
          <p className="text-sm">等待 Agent 打开文件...</p>
        </div>
      </div>
    );
  }

  // 语言标签颜色
  const langColors: Record<string, string> = {
    python: "bg-sky-500/20 text-sky-400",
    javascript: "bg-amber-500/20 text-amber-400",
    typescript: "bg-blue-500/20 text-blue-400",
    html: "bg-orange-500/20 text-orange-400",
    css: "bg-purple-500/20 text-purple-400",
    json: "bg-emerald-500/20 text-emerald-400",
    markdown: "bg-slate-500/20 text-slate-400",
    shell: "bg-green-500/20 text-green-400",
  };

  const langClass = langColors[file.language] || "bg-muted text-muted-foreground";

  return (
    <div className="h-full flex flex-col bg-[oklch(0.1_0.01_260)] rounded-lg overflow-hidden">
      {/* 编辑器头部 - 文件标签 */}
      <div className="flex items-center justify-between px-4 py-2 bg-[oklch(0.13_0.012_260)] border-b border-border/20">
        <div className="flex items-center gap-2">
          <FileCode className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-sm text-foreground font-medium">{file.name}</span>
          <span className={`text-[10px] px-1.5 py-0.5 rounded-md font-mono ${langClass}`}>
            {file.language}
          </span>
        </div>
        <div className="flex items-center gap-2">
          <span className="text-[10px] text-muted-foreground/60">
            {lines.length} 行
          </span>
          <button
            onClick={handleCopy}
            className="p-1 rounded hover:bg-accent/50 transition-colors"
          >
            {copied ? (
              <Check className="w-3.5 h-3.5 text-emerald-400" />
            ) : (
              <Copy className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
        </div>
      </div>

      {/* 代码内容 */}
      <div className="flex-1 overflow-auto">
        <div className="flex font-mono text-[13px] leading-[1.6]">
          {/* 行号 */}
          <div className="sticky left-0 select-none text-right pr-4 pl-4 py-3 bg-[oklch(0.1_0.01_260)] text-muted-foreground/30 border-r border-border/10">
            {lines.map((_, i) => (
              <div key={i}>{i + 1}</div>
            ))}
          </div>

          {/* 代码 */}
          <div className="flex-1 py-3 pl-4 pr-4 text-foreground/90">
            {lines.map((line, i) => (
              <div key={i} className="whitespace-pre hover:bg-accent/10 transition-colors">
                {highlightLine(line, file.language)}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  );
}
