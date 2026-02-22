/**
 * ToolCallCard - 展示 Agent 工具调用过程
 * 设计风格: Glass Workspace 毛玻璃卡片
 * 每个工具调用显示为一个可展开的玻璃卡片
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
  ChevronDown,
  CheckCircle2,
  XCircle,
  Loader2,
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
  read_file: FileText,
  write_file: FilePlus,
};

const STATUS_CONFIG = {
  pending: { color: "text-muted-foreground", bg: "bg-muted/50", label: "等待中" },
  running: { color: "text-primary", bg: "bg-primary/10", label: "执行中" },
  completed: { color: "text-emerald-400", bg: "bg-emerald-500/10", label: "完成" },
  failed: { color: "text-destructive", bg: "bg-destructive/10", label: "失败" },
};

interface ToolCallCardProps {
  toolCall: ToolCall;
}

export default function ToolCallCard({ toolCall }: ToolCallCardProps) {
  const [expanded, setExpanded] = useState(false);
  const Icon = TOOL_ICON_MAP[toolCall.name] || Code;
  const statusConfig = STATUS_CONFIG[toolCall.status];
  const toolName = TOOL_NAMES[toolCall.name] || toolCall.name;

  const args = (toolCall.arguments || {}) as Record<string, unknown>;
  const resultText = typeof toolCall.result === "string"
    ? toolCall.result
    : toolCall.result === undefined || toolCall.result === null
      ? ""
      : JSON.stringify(toolCall.result, null, 2);

  const argsJson = (() => {
    try {
      return JSON.stringify(args, null, 2);
    } catch {
      return "{}";
    }
  })();

  // 格式化参数显示
  const formatArgs = () => {
    if (toolCall.name === "web_search") return (args.query as string) || "(无查询参数)";
    if (toolCall.name === "wide_research") {
      const template = (args.query_template as string) || "";
      const items = Array.isArray(args.items) ? args.items : [];
      return `${template || "(无模板)"} [items=${items.length}]`;
    }
    if (toolCall.name === "spawn_sub_agents") {
      const template = (args.task_template as string) || "";
      const items = Array.isArray(args.items) ? args.items : [];
      return `${template || "(无模板)"} [agents=${items.length}]`;
    }
    if (toolCall.name === "shell_exec") return `$ ${((args.command as string) || "").slice(0, 60)}`;
    if (toolCall.name === "execute_code") {
      const code = (args.code as string) || "";
      if (!code) return "(无代码参数)";
      return code.length > 80 ? code.slice(0, 80) + "..." : code;
    }
    if (toolCall.name === "browser_navigate") return (args.url as string) || "(无 URL 参数)";
    if (toolCall.name === "read_file" || toolCall.name === "write_file") {
      return (args.path as string) || "(无路径参数)";
    }
    return argsJson;
  };

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, ease: [0.23, 1, 0.32, 1] }}
      className="my-2"
    >
      <div
        className={`glass rounded-xl overflow-hidden transition-all duration-300 ${
          toolCall.status === "running" ? "glow-primary" : ""
        }`}
      >
        {/* 头部 */}
        <button
          onClick={() => setExpanded(!expanded)}
          className="w-full flex items-center gap-3 px-4 py-3 hover:bg-white/5 transition-colors"
        >
          <div className={`p-2 rounded-lg ${statusConfig.bg}`}>
            {toolCall.status === "running" ? (
              <Loader2 className={`w-4 h-4 ${statusConfig.color} animate-spin`} />
            ) : (
              <Icon className={`w-4 h-4 ${statusConfig.color}`} />
            )}
          </div>

          <div className="flex-1 text-left">
            <div className="flex items-center gap-2">
              <span className="text-sm font-medium text-foreground">
                {toolName}
              </span>
              <span className={`text-xs ${statusConfig.color}`}>
                {statusConfig.label}
              </span>
            </div>
            <p className="text-xs text-muted-foreground mt-0.5 truncate max-w-[400px] font-mono">
              {formatArgs()}
            </p>
          </div>

          <div className="flex items-center gap-2">
            {toolCall.status === "completed" && (
              <CheckCircle2 className="w-4 h-4 text-emerald-400" />
            )}
            {toolCall.status === "failed" && (
              <XCircle className="w-4 h-4 text-destructive" />
            )}
            <ChevronDown
              className={`w-4 h-4 text-muted-foreground transition-transform duration-200 ${
                expanded ? "rotate-180" : ""
              }`}
            />
          </div>
        </button>

        {/* 展开内容 */}
        <AnimatePresence>
          {expanded && (
            <motion.div
              initial={{ height: 0, opacity: 0 }}
              animate={{ height: "auto", opacity: 1 }}
              exit={{ height: 0, opacity: 0 }}
              transition={{ duration: 0.2 }}
              className="overflow-hidden"
            >
              <div className="px-4 pb-3 border-t border-border/50">
                {/* 参数详情 */}
                <div className="mt-3">
                  <p className="text-xs text-muted-foreground mb-1 font-medium">参数</p>
                  <pre className="text-xs bg-black/30 rounded-lg p-3 overflow-x-auto font-mono text-foreground/80 max-h-40 overflow-y-auto">
                    {argsJson}
                  </pre>
                </div>

                {/* 结果 */}
                {resultText && (
                  <div className="mt-3">
                    <p className="text-xs text-muted-foreground mb-1 font-medium">结果</p>
                    <pre className="text-xs bg-black/30 rounded-lg p-3 overflow-x-auto font-mono text-foreground/80 max-h-60 overflow-y-auto whitespace-pre-wrap">
                      {resultText.length > 2000
                        ? resultText.slice(0, 2000) + "\n... [已截断]"
                        : resultText}
                    </pre>
                  </div>
                )}
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
