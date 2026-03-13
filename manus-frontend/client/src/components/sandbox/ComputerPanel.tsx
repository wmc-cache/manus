/**
 * ComputerPanel - 计算机窗口主面板
 * 整合终端、编辑器、浏览器、文件管理器
 * 
 * 对齐 Manus 1.6 Max 风格：
 * - 面板顶部显示当前 Agent 正在使用的工具
 * - 自动切换到对应的标签页
 * - 更精细的状态指示
 */
import { motion, AnimatePresence } from "framer-motion";
import {
  Terminal,
  FileCode,
  Globe,
  Wifi,
  WifiOff,
  Monitor,
  Maximize2,
  Minimize2,
  X,
  Loader2,
  ExternalLink,
  MonitorPlay,
} from "lucide-react";
import { useState, useEffect, useRef } from "react";
import TerminalWindow from "./TerminalWindow";
import EditorWindow from "./EditorWindow";
import BrowserWindow from "./BrowserWindow";
import FilesWindow from "./FilesWindow";
import VncOverlay from "./VncOverlay";
import type {
  ActiveWindow,
  FileNode,
  FileContent,
  BrowserData,
  ExposedPort,
} from "@/hooks/useSandbox";

/** 工具名称到面板标签页的映射 */
const TOOL_TO_WINDOW: Record<string, ActiveWindow> = {
  shell_exec: "terminal",
  execute_code: "terminal",
  browser_navigate: "browser",
  browser_screenshot: "browser",
  browser_get_content: "browser",
  browser_click: "browser",
  browser_input: "browser",
  browser_scroll: "browser",
  read_file: "editor",
  write_file: "editor",
  edit_file: "editor",
  append_file: "editor",
  list_files: "editor",
  find_files: "editor",
  grep_files: "editor",
};

/** 工具名称到人性化描述的映射 */
const TOOL_ACTIVITY_LABEL: Record<string, string> = {
  shell_exec: "终端",
  execute_code: "终端",
  browser_navigate: "浏览器",
  browser_screenshot: "浏览器",
  browser_get_content: "浏览器",
  browser_click: "浏览器",
  browser_input: "浏览器",
  browser_scroll: "浏览器",
  read_file: "编辑器",
  write_file: "编辑器",
  edit_file: "编辑器",
  append_file: "编辑器",
  list_files: "文件管理器",
  find_files: "文件管理器",
  grep_files: "文件管理器",
  web_search: "网络搜索",
  wide_research: "并行研究",
  spawn_sub_agents: "子代理",
  data_analysis: "数据分析",
};

interface CurrentToolInfo {
  name: string;
  arguments?: Record<string, unknown>;
}

interface ComputerPanelProps {
  connected: boolean;
  activeWindow: ActiveWindow;
  onWindowChange: (window: ActiveWindow) => void;
  terminalOutput: string;
  onTerminalInput?: (data: string) => void;
  browserData: BrowserData | null;
  editorFile: FileContent | null;
  fileTree: FileNode[];
  onFileClick?: (path: string) => void;
  onRefreshFiles?: () => void;
  onDownloadAllFiles?: () => void;
  downloadingAllFiles?: boolean;
  onBrowserClick?: (x: number, y: number, viewportWidth: number, viewportHeight: number) => void;
  onBrowserType?: (text: string, submit?: boolean) => void;
  onBrowserNavigate?: (url: string) => void;
  onBrowserScroll?: (deltaY: number) => void;
  onBrowserKey?: (key: "Enter" | "Tab" | "Escape") => void;
  browserInteractionError?: string | null;
  onClose?: () => void;
  /** 当前正在执行的工具信息 */
  currentTool?: CurrentToolInfo | null;
  /** Agent 是否正在工作 */
  isAgentWorking?: boolean;
  /** 已暴露的端口列表 */
  exposedPorts?: ExposedPort[];
  /** 会话 ID，用于 VNC 连接 */
  conversationId?: string;
}

const tabs: { id: ActiveWindow; label: string; icon: typeof Terminal }[] = [
  { id: "terminal", label: "终端", icon: Terminal },
  { id: "editor", label: "编辑器", icon: FileCode },
  { id: "browser", label: "浏览器", icon: Globe },
];

export default function ComputerPanel({
  connected,
  activeWindow,
  onWindowChange,
  terminalOutput,
  onTerminalInput,
  browserData,
  editorFile,
  fileTree,
  onFileClick,
  onRefreshFiles,
  onDownloadAllFiles,
  downloadingAllFiles = false,
  onBrowserClick,
  onBrowserType,
  onBrowserNavigate,
  onBrowserScroll,
  onBrowserKey,
  browserInteractionError,
  onClose,
  currentTool,
  isAgentWorking = false,
  exposedPorts = [],
  conversationId,
}: ComputerPanelProps) {
  const [isMaximized, setIsMaximized] = useState(false);
  const [vncOpen, setVncOpen] = useState(false);
  const autoSwitchRef = useRef(true);

  // 当工具变化时，自动切换到对应的标签页
  useEffect(() => {
    if (!currentTool || !autoSwitchRef.current) return;
    const targetWindow = TOOL_TO_WINDOW[currentTool.name];
    if (targetWindow && targetWindow !== activeWindow) {
      onWindowChange(targetWindow);
    }
  }, [currentTool, activeWindow, onWindowChange]);

  // 用户手动切换标签页时，暂时禁用自动切换
  const handleWindowChange = (window: ActiveWindow) => {
    autoSwitchRef.current = false;
    onWindowChange(window);
    // 3秒后恢复自动切换
    setTimeout(() => {
      autoSwitchRef.current = true;
    }, 3000);
  };

  // 当前工具的活动标签
  const currentToolLabel = currentTool ? TOOL_ACTIVITY_LABEL[currentTool.name] : null;

  return (
    <motion.div
      initial={{ opacity: 0, x: 40 }}
      animate={{ opacity: 1, x: 0 }}
      exit={{ opacity: 0, x: 40 }}
      transition={{ type: "spring", stiffness: 300, damping: 30 }}
      className={`flex flex-col glass rounded-xl overflow-hidden ${
        isMaximized
          ? "fixed inset-4 z-50"
          : "h-full"
      }`}
    >
      {/* 面板头部 */}
      <div className="flex items-center justify-between px-4 py-2 bg-[oklch(0.16_0.014_260)] border-b border-border/20">
        <div className="flex items-center gap-2">
          <Monitor className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">
            Manus 计算机
          </span>
          {/* 连接状态 */}
          <div className="flex items-center gap-1 ml-1">
            {connected ? (
              <>
                <Wifi className="w-3 h-3 text-emerald-400" />
                <span className="text-[10px] text-emerald-400/80">已连接</span>
              </>
            ) : (
              <>
                <WifiOff className="w-3 h-3 text-red-400" />
                <span className="text-[10px] text-red-400/80">未连接</span>
              </>
            )}
          </div>
        </div>

        <div className="flex items-center gap-1">
          <button
            onClick={() => setVncOpen(true)}
            className="px-2 py-1 rounded text-[10px] transition-colors bg-muted/40 text-muted-foreground hover:bg-primary/20 hover:text-primary"
            title="打开 VNC 远程桌面"
          >
            <span className="inline-flex items-center gap-1">
              <MonitorPlay className="w-3 h-3" />
              VNC
            </span>
          </button>
          <button
            onClick={() => setIsMaximized(!isMaximized)}
            className="p-1 rounded hover:bg-accent/30 transition-colors"
          >
            {isMaximized ? (
              <Minimize2 className="w-3.5 h-3.5 text-muted-foreground" />
            ) : (
              <Maximize2 className="w-3.5 h-3.5 text-muted-foreground" />
            )}
          </button>
          {onClose && (
            <button
              onClick={onClose}
              className="p-1 rounded hover:bg-destructive/20 transition-colors"
            >
              <X className="w-3.5 h-3.5 text-muted-foreground" />
            </button>
          )}
        </div>
      </div>

      {/* 当前工具指示条 - 对齐 Manus 1.6 Max */}
      <AnimatePresence>
        {isAgentWorking && currentTool && (
          <motion.div
            initial={{ height: 0, opacity: 0 }}
            animate={{ height: "auto", opacity: 1 }}
            exit={{ height: 0, opacity: 0 }}
            transition={{ duration: 0.2 }}
            className="overflow-hidden"
          >
            <div className="flex items-center gap-2 px-4 py-1.5 bg-primary/8 border-b border-primary/15">
              <Loader2 className="w-3 h-3 text-primary animate-spin" />
              <span className="text-xs text-primary/90 font-medium">
                Manus 正在使用{currentToolLabel || currentTool.name}
              </span>
              {/* 显示工具的关键参数 */}
              {currentTool.arguments && (
                <span className="text-[10px] text-primary/50 font-mono truncate max-w-[200px]">
                  {formatToolHint(currentTool.name, currentTool.arguments)}
                </span>
              )}
            </div>
          </motion.div>
        )}
      </AnimatePresence>

      {/* 已暴露端口链接栏 */}
      {exposedPorts.length > 0 && (
        <div className="flex items-center gap-2 px-4 py-1.5 bg-[oklch(0.13_0.02_200)] border-b border-border/20">
          <ExternalLink className="w-3 h-3 text-sky-400 flex-shrink-0" />
          <span className="text-[10px] text-sky-300/80 flex-shrink-0">已暴露:</span>
          <div className="flex items-center gap-2 overflow-x-auto">
            {exposedPorts.map((ep) => (
              <a
                key={ep.port}
                href={ep.proxyPath}
                target="_blank"
                rel="noopener noreferrer"
                className="inline-flex items-center gap-1 px-2 py-0.5 rounded bg-sky-500/15 text-sky-300 text-[10px] font-mono hover:bg-sky-500/25 transition-colors whitespace-nowrap"
                title={`点击在新标签页打开 ${ep.label}`}
              >
                <ExternalLink className="w-2.5 h-2.5" />
                {ep.label} (:{ep.port})
              </a>
            ))}
          </div>
        </div>
      )}

      {/* 标签栏 */}
      <div className="flex items-center gap-1 px-3 py-1.5 bg-[oklch(0.14_0.013_260)] border-b border-border/15">
        {tabs.map((tab) => {
          const isActive = activeWindow === tab.id;
          const Icon = tab.icon;

          // 活跃指示 - 终端有输出、编辑器有文件、浏览器有截图
          let hasActivity = false;
          if (tab.id === "terminal" && terminalOutput) hasActivity = true;
          if (tab.id === "editor" && (editorFile || fileTree.length > 0)) hasActivity = true;
          if (tab.id === "browser" && browserData?.screenshot) hasActivity = true;

          // 当前工具是否指向这个标签页
          const isToolTarget = currentTool ? TOOL_TO_WINDOW[currentTool.name] === tab.id : false;

          return (
            <button
              key={tab.id}
              onClick={() => handleWindowChange(tab.id)}
              className={`relative flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-all ${
                isActive
                  ? "bg-accent/40 text-foreground"
                  : "text-muted-foreground hover:text-foreground hover:bg-accent/20"
              }`}
            >
              <Icon className="w-3.5 h-3.5" />
              <span>{tab.label}</span>

              {/* 活跃指示点 */}
              {hasActivity && !isActive && (
                <div className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-primary animate-pulse" />
              )}

              {/* 当前工具指向该标签的脉冲指示 */}
              {isToolTarget && isAgentWorking && !isActive && (
                <div className="absolute top-1 right-1 w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />
              )}
            </button>
          );
        })}
      </div>

      {/* 窗口内容 */}
      <div className="flex-1 p-2 overflow-hidden">
        <AnimatePresence mode="wait">
          <motion.div
            key={activeWindow}
            initial={{ opacity: 0, y: 8 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -8 }}
            transition={{ duration: 0.15 }}
            className="h-full"
          >
            {activeWindow === "terminal" && (
              <TerminalWindow
                output={terminalOutput}
                onInput={onTerminalInput}
              />
            )}
            {activeWindow === "editor" && (
              <div className="h-full flex gap-2">
                <div className="w-[38%] min-w-[180px] max-w-[320px]">
                  <FilesWindow
                    tree={fileTree}
                    onFileClick={onFileClick}
                    onRefresh={onRefreshFiles}
                    onDownloadAll={onDownloadAllFiles}
                    downloadingAll={downloadingAllFiles}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <EditorWindow file={editorFile} />
                </div>
              </div>
            )}
            {activeWindow === "browser" && (
              <BrowserWindow
                data={browserData}
                conversationId={conversationId}
                onPageClick={onBrowserClick}
                onTypeText={onBrowserType}
                onNavigate={onBrowserNavigate}
                onScrollPage={onBrowserScroll}
                onPressKey={onBrowserKey}
                interactionError={browserInteractionError}
              />
            )}
          </motion.div>
        </AnimatePresence>
      </div>

      {/* VNC 远程桌面覆盖层 */}
      <VncOverlay
        conversationId={conversationId}
        open={vncOpen}
        onClose={() => setVncOpen(false)}
      />
    </motion.div>
  );
}

/** 格式化工具提示信息 */
function formatToolHint(name: string, args: Record<string, unknown>): string {
  switch (name) {
    case "shell_exec": {
      const cmd = ((args.command as string) || "").trim();
      return cmd.length > 30 ? cmd.slice(0, 27) + "..." : cmd;
    }
    case "browser_navigate":
      return (args.url as string) || "";
    case "browser_click":
      return `(${args.x || 0}, ${args.y || 0})`;
    case "browser_input": {
      const text = (args.text as string) || "";
      return text.length > 20 ? `"${text.slice(0, 17)}..."` : `"${text}"`;
    }
    case "read_file":
    case "write_file":
    case "edit_file":
    case "append_file": {
      const path = (args.path as string) || "";
      const parts = path.split("/");
      return parts[parts.length - 1] || path;
    }
    case "web_search":
      return (args.query as string) || "";
    case "find_files":
      return (args.pattern as string) || "*";
    case "grep_files":
      return `/${(args.regex as string) || ""}/`;
    default:
      return "";
  }
}
