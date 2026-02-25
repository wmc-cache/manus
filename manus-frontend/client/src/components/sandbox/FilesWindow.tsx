/**
 * FilesWindow - 文件管理器窗口组件
 * 展示沙箱中的文件树结构
 * 
 * 风格: 深色文件管理器 + 图标 + 可展开目录
 */
import { useState } from "react";
import {
  Folder,
  FolderOpen,
  FileText,
  FileCode,
  FileJson,
  FileImage,
  File,
  ChevronRight,
  ChevronDown,
  HardDrive,
  RefreshCw,
  Download,
} from "lucide-react";
import type { FileNode } from "@/hooks/useSandbox";

interface FilesWindowProps {
  tree: FileNode[];
  onFileClick?: (path: string) => void;
  onRefresh?: () => void;
  onDownloadAll?: () => void;
  downloadingAll?: boolean;
}

function getFileIcon(icon: string, isOpen?: boolean) {
  const size = "w-4 h-4";
  switch (icon) {
    case "folder":
      return isOpen ? (
        <FolderOpen className={`${size} text-amber-400`} />
      ) : (
        <Folder className={`${size} text-amber-400/80`} />
      );
    case "python":
      return <FileCode className={`${size} text-sky-400`} />;
    case "javascript":
    case "typescript":
    case "react":
      return <FileCode className={`${size} text-amber-300`} />;
    case "html":
    case "css":
      return <FileCode className={`${size} text-orange-400`} />;
    case "json":
      return <FileJson className={`${size} text-emerald-400`} />;
    case "markdown":
    case "text":
      return <FileText className={`${size} text-slate-400`} />;
    case "image":
      return <FileImage className={`${size} text-purple-400`} />;
    default:
      return <File className={`${size} text-muted-foreground`} />;
  }
}

function FileTreeItem({
  node,
  depth,
  onFileClick,
}: {
  node: FileNode;
  depth: number;
  onFileClick?: (path: string) => void;
}) {
  const [isOpen, setIsOpen] = useState(depth < 2);
  const isDir = node.type === "directory";

  const handleClick = () => {
    if (isDir) {
      setIsOpen(!isOpen);
    } else {
      onFileClick?.(node.path);
    }
  };

  return (
    <div>
      <div
        className="flex items-center gap-1 py-1 px-2 rounded-md cursor-pointer hover:bg-accent/30 transition-colors group"
        style={{ paddingLeft: `${depth * 16 + 8}px` }}
        onClick={handleClick}
      >
        {/* 展开/折叠图标 */}
        {isDir ? (
          isOpen ? (
            <ChevronDown className="w-3.5 h-3.5 text-muted-foreground/50" />
          ) : (
            <ChevronRight className="w-3.5 h-3.5 text-muted-foreground/50" />
          )
        ) : (
          <span className="w-3.5" />
        )}

        {/* 文件图标 */}
        {getFileIcon(node.icon, isOpen)}

        {/* 文件名 */}
        <span className="text-sm text-foreground/85 truncate group-hover:text-foreground transition-colors">
          {node.name}
        </span>

        {/* 文件大小 */}
        {!isDir && node.size !== undefined && (
          <span className="text-[10px] text-muted-foreground/40 ml-auto">
            {node.size > 1024
              ? `${(node.size / 1024).toFixed(1)}KB`
              : `${node.size}B`}
          </span>
        )}
      </div>

      {/* 子节点 */}
      {isDir && isOpen && node.children && (
        <div>
          {node.children.map((child) => (
            <FileTreeItem
              key={child.path}
              node={child}
              depth={depth + 1}
              onFileClick={onFileClick}
            />
          ))}
        </div>
      )}
    </div>
  );
}

export default function FilesWindow({
  tree,
  onFileClick,
  onRefresh,
  onDownloadAll,
  downloadingAll = false,
}: FilesWindowProps) {
  return (
    <div className="h-full flex flex-col bg-[oklch(0.1_0.01_260)] rounded-lg overflow-hidden">
      {/* 头部 */}
      <div className="flex items-center justify-between px-4 py-2.5 bg-[oklch(0.13_0.012_260)] border-b border-border/20">
        <div className="flex items-center gap-2">
          <HardDrive className="w-3.5 h-3.5 text-muted-foreground" />
          <span className="text-xs text-muted-foreground font-mono">
            /workspace
          </span>
        </div>
        <div className="flex items-center gap-1">
          <button
            onClick={onDownloadAll}
            className="p-1 rounded hover:bg-accent/30 transition-colors disabled:opacity-50 disabled:cursor-not-allowed"
            title="下载全部文件"
            disabled={!onDownloadAll || downloadingAll}
          >
            <Download className={`w-3.5 h-3.5 text-muted-foreground ${downloadingAll ? "animate-pulse" : ""}`} />
          </button>
          <button
            onClick={onRefresh}
            className="p-1 rounded hover:bg-accent/30 transition-colors"
            title="刷新文件列表"
          >
            <RefreshCw className="w-3.5 h-3.5 text-muted-foreground" />
          </button>
        </div>
      </div>

      {/* 文件树 */}
      <div className="flex-1 overflow-y-auto py-2">
        {tree.length === 0 ? (
          <div className="flex items-center justify-center h-full text-muted-foreground/50">
            <div className="text-center">
              <Folder className="w-10 h-10 mx-auto mb-2 opacity-30" />
              <p className="text-sm">工作目录为空</p>
              <p className="text-xs mt-1">Agent 创建的文件将显示在这里</p>
            </div>
          </div>
        ) : (
          tree.map((node) => (
            <FileTreeItem
              key={node.path}
              node={node}
              depth={0}
              onFileClick={onFileClick}
            />
          ))
        )}
      </div>
    </div>
  );
}
