/**
 * ToolCallCard - 工具调用展示（紧凑单行标签）
 * 
 * 对齐 Manus 1.6 Max 风格：
 * - 默认以紧凑的单行标签展示（图标 + 工具名 + 状态 + 参数摘要）
 * - 点击可展开查看详细参数和结果
 * - 运行中的工具有微妙的动画效果
 */
import { useState } from "react";
import { motion, AnimatePresence } from "framer-motion";
import {
  Globe,
  ListTodo,
  Code,
  FileText,
  FilePlus,
  Terminal,
  Camera,
  ChevronRight,
  CheckCircle2,
  XCircle,
  Loader2,
  Search,
  FileSearch,
  BarChart3,
  MousePointer,
  Keyboard,
  ArrowDown,
  FolderTree,
  FileEdit,
} from "lucide-react";
import type { ToolCall } from "@/types";
import { TOOL_NAMES } from "@/types";

const TOOL_ICON_MAP: Record<string, React.ElementType> = {
  web_search: Globe,
  wide_research: ListTodo,
  spawn_sub_agents: ListTodo,
  shell_exec: Terminal,
  execute_code: Code,
  browser_navigate: Globe,
  browser_screenshot: Camera,
  browser_get_content: FileText,
  browser_click: MousePointer,
  browser_input: Keyboard,
  browser_scroll: ArrowDown,
  read_file: FileText,
  write_file: FilePlus,
  edit_file: FileEdit,
  append_file: FilePlus,
  list_files: FolderTree,
  find_files: Search,
  grep_files: FileSearch,
  data_analysis: BarChart3,
};

const INLINE_IMAGE_TAG_RE = /\[IMAGE:(data:image\/[a-zA-Z0-9.+-]+;base64,[A-Za-z0-9+/=]+)\]/g;

/** 简化 URL 显示 */
function simplifyUrl(url: string): string {
  try {
    const u = new URL(url);
    const host = u.hostname.replace(/^www\./, "");
    const path = u.pathname === "/" ? "" : u.pathname;
    const display = host + path;
    return display.length > 50 ? display.slice(0, 47) + "..." : display;
  } catch {
    return url.length > 50 ? url.slice(0, 47) + "..." : url;
  }
}

/** 智能格式化 shell 命令 */
function formatShellCommand(cmd: string): string {
  if (!cmd) return "执行命令";
  // 处理链式命令，只显示最后一个有意义的命令
  const parts = cmd.split(/\s*&&\s*|\s*;\s*/);
  const lastCmd = parts[parts.length - 1]?.trim() || cmd;
  // 如果是 cd + 其他命令，显示其他命令
  if (parts.length > 1 && parts[0].trim().startsWith("cd ")) {
    const rest = parts.slice(1).join(" && ").trim();
    if (rest && rest.length <= 55) return `$ ${rest}`;
    if (rest) return `$ ${rest.slice(0, 52)}...`;
  }
  if (lastCmd.length <= 55) return `$ ${lastCmd}`;
  return `$ ${lastCmd.slice(0, 52)}...`;
}

/** 为每种工具生成人性化的单行描述 */
function formatToolDescription(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case "web_search":
      return (args.query as string) || "搜索中...";
    case "wide_research": {
      const template = (args.query_template as string) || "";
      const items = Array.isArray(args.items) ? args.items : [];
      return template ? `${template} (${items.length} 项)` : `并行研究 ${items.length} 项`;
    }
    case "spawn_sub_agents": {
      const tpl = (args.task_template as string) || "";
      const its = Array.isArray(args.items) ? args.items : [];
      return tpl ? `${tpl} (${its.length} agents)` : `启动 ${its.length} 个子代理`;
    }
    case "shell_exec":
      return formatShellCommand(((args.command as string) || "").trim());
    case "execute_code": {
      const code = (args.code as string) || "";
      const lang = (args.language as string) || "python";
      if (!code) return `${lang} 代码`;
      // 尝试提取有意义的首行（跳过 import 和空行）
      const lines = code.split("\n").map(l => l.trim()).filter(l => l && !l.startsWith("import ") && !l.startsWith("from ") && !l.startsWith("#"));
      const meaningful = lines[0] || code.split("\n")[0].trim();
      return meaningful.length > 50 ? meaningful.slice(0, 47) + "..." : meaningful;
    }
    case "browser_navigate":
      return simplifyUrl((args.url as string) || "");
    case "browser_screenshot":
      return "截取页面截图";
    case "browser_get_content":
      return "获取页面内容";
    case "browser_click": {
      const selector = (args.selector as string) || "";
      if (selector) return selector.length > 40 ? selector.slice(0, 37) + "..." : selector;
      return `点击 (${args.x || 0}, ${args.y || 0})`;
    }
    case "browser_input": {
      const text = (args.text as string) || "";
      return text.length > 40 ? `"${text.slice(0, 37)}..."` : `"${text}"`;
    }
    case "browser_scroll": {
      const dir = (args.direction as string) || "down";
      const dirMap: Record<string, string> = { up: "向上滚动", down: "向下滚动", left: "向左滚动", right: "向右滚动" };
      return dirMap[dir] || dir;
    }
    case "read_file": {
      const path = (args.path as string) || "";
      return path ? extractFileName(path) : "读取文件";
    }
    case "write_file": {
      const path = (args.path as string) || "";
      const content = (args.content as string) || "";
      const lineCount = content.split("\n").length;
      const fileName = path ? extractFileName(path) : "文件";
      return lineCount > 1 ? `${fileName} (${lineCount} 行)` : fileName;
    }
    case "edit_file": {
      const path = (args.path as string) || "";
      return path ? extractFileName(path) : "编辑文件";
    }
    case "append_file": {
      const path = (args.path as string) || "";
      return path ? extractFileName(path) : "追加文件";
    }
    case "list_files":
      return extractFileName((args.path as string) || ".");
    case "find_files": {
      const pattern = (args.pattern as string) || "*";
      const scope = (args.path as string) || ".";
      return `${pattern} in ${extractFileName(scope)}`;
    }
    case "grep_files": {
      const regex = (args.regex as string) || "";
      const scope = (args.scope as string) || "**/*";
      return `/${regex}/ in ${extractFileName(scope)}`;
    }
    case "data_analysis": {
      const code = (args.code as string) || "";
      const lines = code.split("\n").map(l => l.trim()).filter(l => l && !l.startsWith("import ") && !l.startsWith("from ") && !l.startsWith("#"));
      const meaningful = lines[0] || "数据分析";
      return meaningful.length > 50 ? meaningful.slice(0, 47) + "..." : meaningful;
    }
    default: {
      const keys = Object.keys(args);
      if (keys.length === 0) return name;
      const first = args[keys[0]];
      if (typeof first === "string" && first.length <= 50) return first;
      if (typeof first === "string") return first.slice(0, 47) + "...";
      return name;
    }
  }
}

/** 从路径中提取文件名 */
function extractFileName(path: string): string {
  if (!path) return "";
  const parts = path.split("/");
  const name = parts[parts.length - 1] || path;
  // 如果路径较短，显示完整路径
  if (path.length <= 45) return path;
  // 否则显示 .../dir/filename
  if (parts.length >= 3) {
    return `.../${parts[parts.length - 2]}/${name}`;
  }
  return name;
}

function parseToolResult(resultText: string): { text: string; images: string[] } {
  if (!resultText) {
    return { text: "", images: [] };
  }

  const images = Array.from(resultText.matchAll(INLINE_IMAGE_TAG_RE), (match) => match[1]);
  const text = resultText.replace(INLINE_IMAGE_TAG_RE, "").trim();

  return { text, images };
}

interface ToolCallCardProps {
  toolCall: ToolCall;
}

export default function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const Icon = TOOL_ICON_MAP[toolCall.name] || Code;
  const toolName = TOOL_NAMES[toolCall.name] || toolCall.name;
  const args = (toolCall.arguments || {}) as Record<string, unknown>;
  const description = formatToolDescription(toolCall.name, args);

  const resultText = typeof toolCall.result === "string"
    ? toolCall.result
    : toolCall.result === undefined || toolCall.result === null
      ? ""
      : JSON.stringify(toolCall.result, null, 2);
  const parsedResult = parseToolResult(resultText);

  const argsJson = (() => {
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return "{}";
    }
  })();

  const isRunning = toolCall.status === "running";
  const isCompleted = toolCall.status === "completed";
  const isFailed = toolCall.status === "failed";

  return (
    <div className="my-0.5">
      {/* 紧凑单行标签 */}
      <button
        onClick={() => setExpanded(!expanded)}
        className={`group w-full flex items-center gap-2 px-3 py-1.5 rounded-lg text-left transition-all duration-200 ${
          isRunning
            ? "bg-primary/8 hover:bg-primary/12"
            : isFailed
              ? "bg-destructive/5 hover:bg-destructive/10"
              : "hover:bg-accent/30"
        }`}
      >
        {/* 状态图标 */}
        <div className="shrink-0 flex items-center justify-center w-5 h-5">
          {isRunning ? (
            <Loader2 className="w-3.5 h-3.5 text-primary animate-spin" />
          ) : isCompleted ? (
            <CheckCircle2 className="w-3.5 h-3.5 text-emerald-500" />
          ) : isFailed ? (
            <XCircle className="w-3.5 h-3.5 text-destructive" />
          ) : (
            <Icon className="w-3.5 h-3.5 text-muted-foreground/60" />
          )}
        </div>

        {/* 工具名称 */}
        <span className={`shrink-0 text-xs font-medium ${
          isRunning ? "text-primary" : isFailed ? "text-destructive" : "text-foreground/80"
        }`}>
          {toolName}
        </span>

        {/* 分隔点 */}
        <span className="shrink-0 text-muted-foreground/30 text-xs">·</span>

        {/* 参数描述 */}
        <span className="flex-1 text-xs text-muted-foreground truncate font-mono">
          {description}
        </span>

        {/* 展开箭头 */}
        <ChevronRight
          className={`shrink-0 w-3 h-3 text-muted-foreground/40 transition-transform duration-200 opacity-0 group-hover:opacity-100 ${
            expanded ? "rotate-90" : ""
          }`}
        />
      </button>

      {/* 展开详情 */}
      <AnimatePresence>
        {expanded && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.15 }}
            className="overflow-hidden"
          >
            <div className="ml-7 mr-2 mt-1 mb-2 rounded-lg border border-border/30 bg-background/40 overflow-hidden">
              {/* 参数 */}
              <div className="px-3 py-2">
                <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1">参数</p>
                <pre className="text-[11px] bg-black/20 rounded-md p-2 overflow-x-auto font-mono text-foreground/70 max-h-32 overflow-y-auto leading-relaxed">
                  {argsJson}
                </pre>
              </div>

              {/* 结果 */}
              {(parsedResult.text || parsedResult.images.length > 0) && (
                <div className="px-3 py-2 border-t border-border/20">
                  <p className="text-[10px] text-muted-foreground/60 uppercase tracking-wider mb-1">结果</p>
                  {parsedResult.text && (
                    <pre className="text-[11px] bg-black/20 rounded-md p-2 overflow-x-auto font-mono text-foreground/70 max-h-48 overflow-y-auto whitespace-pre-wrap leading-relaxed">
                      {parsedResult.text.length > 2000
                        ? parsedResult.text.slice(0, 2000) + "\n... [已截断]"
                        : parsedResult.text}
                    </pre>
                  )}
                  {parsedResult.images.length > 0 && (
                    <div className={`${parsedResult.text ? "mt-2" : ""} space-y-2`}>
                      {parsedResult.images.map((src, index) => (
                        <img
                          key={`${toolCall.id || toolCall.name}-image-${index}`}
                          src={src}
                          alt={`${toolName} 结果截图 ${index + 1}`}
                          className="block max-w-full rounded-md border border-border/30 bg-black/20"
                        />
                      ))}
                    </div>
                  )}
                </div>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>
    </div>
  );
}
