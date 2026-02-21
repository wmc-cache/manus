/**
 * ComputerPanel - 计算机窗口主面板
 * 整合终端、编辑器、浏览器、文件管理器
 * 
 * 风格: 毛玻璃面板 + 标签页切换 + 状态指示器
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
} from "lucide-react";
import { useState } from "react";
import TerminalWindow from "./TerminalWindow";
import EditorWindow from "./EditorWindow";
import BrowserWindow from "./BrowserWindow";
import FilesWindow from "./FilesWindow";
import type { ActiveWindow, FileNode, FileContent, BrowserData } from "@/hooks/useSandbox";

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
  onClose?: () => void;
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
  onClose,
}: ComputerPanelProps) {
  const [isMaximized, setIsMaximized] = useState(false);

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
      <div className="flex items-center justify-between px-4 py-2.5 bg-[oklch(0.16_0.014_260)] border-b border-border/20">
        <div className="flex items-center gap-2">
          <Monitor className="w-4 h-4 text-primary" />
          <span className="text-sm font-semibold text-foreground">
            Manus 计算机
          </span>
          {/* 连接状态 */}
          <div className="flex items-center gap-1 ml-2">
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

          return (
            <button
              key={tab.id}
              onClick={() => onWindowChange(tab.id)}
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
              <TerminalWindow output={terminalOutput} onInput={onTerminalInput} />
            )}
            {activeWindow === "editor" && (
              <div className="h-full flex gap-2">
                <div className="w-[38%] min-w-[180px] max-w-[320px]">
                  <FilesWindow
                    tree={fileTree}
                    onFileClick={onFileClick}
                    onRefresh={onRefreshFiles}
                  />
                </div>
                <div className="flex-1 min-w-0">
                  <EditorWindow file={editorFile} />
                </div>
              </div>
            )}
            {activeWindow === "browser" && (
              <BrowserWindow data={browserData} />
            )}
          </motion.div>
        </AnimatePresence>
      </div>
    </motion.div>
  );
}
