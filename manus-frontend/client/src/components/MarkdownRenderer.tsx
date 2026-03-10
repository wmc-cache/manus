/**
 * MarkdownRenderer - 增强版 Markdown 渲染器
 * 
 * 功能：
 * - 代码块自动语法高亮 (react-syntax-highlighter)
 * - 内联代码样式
 * - 保留 Streamdown 的流式渲染能力
 * - 支持表格、链接、列表等标准 Markdown 元素
 */
import { Suspense, lazy, useMemo } from "react";

interface MarkdownRendererProps {
  content: string;
  streaming?: boolean;
}

const CodeBlock = lazy(() => import("./CodeBlock"));

function CodeBlockFallback({ code }: { code: string }) {
  return (
    <pre className="overflow-x-auto rounded-lg border border-white/10 bg-black/40 p-3 font-mono text-xs text-white/70">
      <code>{code}</code>
    </pre>
  );
}

// Parse markdown content into segments (text and code blocks)
interface Segment {
  type: "text" | "code";
  content: string;
  language?: string;
}

function parseMarkdown(content: string): Segment[] {
  const segments: Segment[] = [];
  const codeBlockRegex = /```(\w*)\n([\s\S]*?)```/g;
  let lastIndex = 0;
  let match;

  while ((match = codeBlockRegex.exec(content)) !== null) {
    // Text before code block
    if (match.index > lastIndex) {
      const text = content.slice(lastIndex, match.index);
      if (text.trim()) {
        segments.push({ type: "text", content: text });
      }
    }

    // Code block
    segments.push({
      type: "code",
      content: match[2],
      language: match[1] || "text",
    });

    lastIndex = match.index + match[0].length;
  }

  // Remaining text after last code block
  if (lastIndex < content.length) {
    const text = content.slice(lastIndex);
    if (text.trim()) {
      segments.push({ type: "text", content: text });
    }
  }

  // If no segments found, treat entire content as text
  if (segments.length === 0 && content.trim()) {
    segments.push({ type: "text", content });
  }

  return segments;
}

/**
 * Simple inline markdown renderer for text segments.
 * Handles: bold, italic, inline code, links, headers, lists, tables
 */
function renderTextSegment(text: string): JSX.Element {
  // Process line by line for block elements
  const lines = text.split("\n");
  const elements: JSX.Element[] = [];
  let inTable = false;
  let tableRows: string[][] = [];
  let tableHeader: string[] = [];

  const flushTable = () => {
    if (tableHeader.length > 0 || tableRows.length > 0) {
      elements.push(
        <div key={`table-${elements.length}`} className="overflow-x-auto my-2">
          <table className="min-w-full text-xs border border-white/10 rounded">
            {tableHeader.length > 0 && (
              <thead>
                <tr className="bg-white/5">
                  {tableHeader.map((h, i) => (
                    <th key={i} className="px-3 py-1.5 text-left border-b border-white/10 font-medium text-white/70">
                      {renderInline(h.trim())}
                    </th>
                  ))}
                </tr>
              </thead>
            )}
            <tbody>
              {tableRows.map((row, ri) => (
                <tr key={ri} className="border-b border-white/5 hover:bg-white/5">
                  {row.map((cell, ci) => (
                    <td key={ci} className="px-3 py-1.5 text-white/60">
                      {renderInline(cell.trim())}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      );
      tableHeader = [];
      tableRows = [];
    }
    inTable = false;
  };

  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];

    // Table detection
    if (line.includes("|") && line.trim().startsWith("|")) {
      const cells = line.split("|").filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);
      
      // Check if this is a separator row (---|---|---)
      if (cells.every(c => /^[\s\-:]+$/.test(c))) {
        inTable = true;
        continue;
      }

      if (!inTable && tableHeader.length === 0) {
        tableHeader = cells;
      } else {
        tableRows.push(cells);
        inTable = true;
      }
      continue;
    } else if (inTable) {
      flushTable();
    }

    // Headers
    const headerMatch = line.match(/^(#{1,6})\s+(.+)/);
    if (headerMatch) {
      const level = headerMatch[1].length;
      const sizes: Record<number, string> = {
        1: "text-lg font-bold",
        2: "text-base font-bold",
        3: "text-sm font-semibold",
        4: "text-sm font-medium",
        5: "text-xs font-medium",
        6: "text-xs font-medium",
      };
      elements.push(
        <div key={`h-${i}`} className={`${sizes[level] || sizes[3]} text-white/90 mt-3 mb-1`}>
          {renderInline(headerMatch[2])}
        </div>
      );
      continue;
    }

    // Unordered list
    if (/^\s*[-*+]\s/.test(line)) {
      const indent = line.match(/^(\s*)/)?.[1].length || 0;
      const content = line.replace(/^\s*[-*+]\s/, "");
      elements.push(
        <div key={`li-${i}`} className="flex gap-1.5" style={{ paddingLeft: `${indent * 4 + 4}px` }}>
          <span className="text-white/30 mt-0.5">•</span>
          <span>{renderInline(content)}</span>
        </div>
      );
      continue;
    }

    // Ordered list
    const olMatch = line.match(/^\s*(\d+)\.\s(.+)/);
    if (olMatch) {
      elements.push(
        <div key={`ol-${i}`} className="flex gap-1.5 pl-1">
          <span className="text-white/40 min-w-[1.2em] text-right">{olMatch[1]}.</span>
          <span>{renderInline(olMatch[2])}</span>
        </div>
      );
      continue;
    }

    // Blockquote
    if (line.startsWith("> ")) {
      elements.push(
        <div key={`bq-${i}`} className="border-l-2 border-primary/40 pl-3 py-0.5 text-white/60 italic">
          {renderInline(line.slice(2))}
        </div>
      );
      continue;
    }

    // Empty line
    if (!line.trim()) {
      elements.push(<div key={`br-${i}`} className="h-2" />);
      continue;
    }

    // Regular paragraph
    elements.push(
      <div key={`p-${i}`} className="leading-relaxed">
        {renderInline(line)}
      </div>
    );
  }

  // Flush any remaining table
  if (inTable || tableHeader.length > 0) {
    flushTable();
  }

  return <>{elements}</>;
}

/**
 * Render inline markdown: bold, italic, inline code, links
 */
function renderInline(text: string): (string | JSX.Element)[] {
  const parts: (string | JSX.Element)[] = [];
  // Pattern: **bold**, *italic*, `code`, [text](url)
  const regex = /(\*\*(.+?)\*\*)|(\*(.+?)\*)|(`(.+?)`)|(\[(.+?)\]\((.+?)\))/g;
  let lastIndex = 0;
  let match;
  let keyIdx = 0;

  while ((match = regex.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index));
    }

    if (match[1]) {
      // Bold
      parts.push(<strong key={`b-${keyIdx++}`} className="font-semibold text-white/90">{match[2]}</strong>);
    } else if (match[3]) {
      // Italic
      parts.push(<em key={`i-${keyIdx++}`} className="italic text-white/70">{match[4]}</em>);
    } else if (match[5]) {
      // Inline code
      parts.push(
        <code key={`c-${keyIdx++}`} className="px-1.5 py-0.5 rounded bg-black/40 text-primary/90 font-mono text-[11px]">
          {match[6]}
        </code>
      );
    } else if (match[7]) {
      // Link
      parts.push(
        <a key={`a-${keyIdx++}`} href={match[9]} target="_blank" rel="noopener noreferrer"
           className="text-primary hover:underline">
          {match[8]}
        </a>
      );
    }

    lastIndex = match.index + match[0].length;
  }

  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex));
  }

  return parts.length > 0 ? parts : [text];
}

export default function MarkdownRenderer({ content, streaming = false }: MarkdownRendererProps) {
  const segments = useMemo(() => parseMarkdown(content), [content]);

  return (
    <div className="text-sm leading-relaxed text-white/80">
      {segments.map((segment, idx) => {
        if (segment.type === "code") {
          return (
            <Suspense key={`code-${idx}`} fallback={<CodeBlockFallback code={segment.content} />}>
              <CodeBlock
                code={segment.content}
                language={segment.language}
              />
            </Suspense>
          );
        }
        return (
          <div key={`text-${idx}`}>
            {renderTextSegment(segment.content)}
          </div>
        );
      })}
      {streaming && (
        <span className="inline-block w-1.5 h-4 bg-primary/60 animate-pulse ml-0.5 rounded-sm" />
      )}
    </div>
  );
}
