/**
 * CodeBlock - 代码语法高亮组件
 * 
 * 功能：
 * - 自动检测语言并应用语法高亮
 * - 一键复制代码
 * - 暗色主题，与整体 UI 风格一致
 */
import { useState, useCallback } from "react";
import { Prism as SyntaxHighlighter } from "react-syntax-highlighter";
import { vscDarkPlus } from "react-syntax-highlighter/dist/esm/styles/prism";
import { Copy, Check } from "lucide-react";

interface CodeBlockProps {
  code: string;
  language?: string;
  showLineNumbers?: boolean;
  maxHeight?: string;
}

export default function CodeBlock({
  code,
  language = "text",
  showLineNumbers = true,
  maxHeight = "400px",
}: CodeBlockProps) {
  const [copied, setCopied] = useState(false);

  const handleCopy = useCallback(async () => {
    try {
      await navigator.clipboard.writeText(code);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      // Fallback for older browsers
      const textarea = document.createElement("textarea");
      textarea.value = code;
      document.body.appendChild(textarea);
      textarea.select();
      document.execCommand("copy");
      document.body.removeChild(textarea);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    }
  }, [code]);

  // Normalize language name
  const normalizedLang = normalizeLanguage(language);

  return (
    <div className="relative group rounded-lg overflow-hidden border border-white/5 my-2">
      {/* Header bar */}
      <div className="flex items-center justify-between px-3 py-1.5 bg-black/60 border-b border-white/5">
        <span className="text-[10px] font-mono text-white/40 uppercase tracking-wider">
          {normalizedLang}
        </span>
        <button
          onClick={handleCopy}
          className="flex items-center gap-1 text-[10px] text-white/40 hover:text-white/70 transition-colors"
          title="复制代码"
        >
          {copied ? (
            <>
              <Check className="w-3 h-3 text-emerald-400" />
              <span className="text-emerald-400">已复制</span>
            </>
          ) : (
            <>
              <Copy className="w-3 h-3" />
              <span>复制</span>
            </>
          )}
        </button>
      </div>

      {/* Code content */}
      <div style={{ maxHeight, overflow: "auto" }}>
        <SyntaxHighlighter
          language={normalizedLang}
          style={vscDarkPlus}
          showLineNumbers={showLineNumbers && code.split("\n").length > 3}
          wrapLines
          customStyle={{
            margin: 0,
            padding: "12px 16px",
            background: "rgba(0, 0, 0, 0.4)",
            fontSize: "12px",
            lineHeight: "1.6",
          }}
          lineNumberStyle={{
            minWidth: "2.5em",
            paddingRight: "1em",
            color: "rgba(255, 255, 255, 0.15)",
            fontSize: "11px",
          }}
        >
          {code.trim()}
        </SyntaxHighlighter>
      </div>
    </div>
  );
}

/**
 * Normalize language identifiers for syntax highlighting
 */
function normalizeLanguage(lang: string): string {
  const langMap: Record<string, string> = {
    js: "javascript",
    ts: "typescript",
    tsx: "tsx",
    jsx: "jsx",
    py: "python",
    rb: "ruby",
    sh: "bash",
    shell: "bash",
    zsh: "bash",
    yml: "yaml",
    md: "markdown",
    dockerfile: "docker",
    "c++": "cpp",
    "c#": "csharp",
    "": "text",
  };
  const lower = lang.toLowerCase().trim();
  return langMap[lower] || lower || "text";
}
