/**
 * Sidebar - 对话列表侧边栏
 * 设计风格: 深色毛玻璃侧边栏
 */
import { motion } from "framer-motion";
import {
  Plus,
  MessageSquare,
  Settings,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { toast } from "sonner";

interface SidebarProps {
  onNewChat: () => void;
  isCollapsed?: boolean;
}

export default function Sidebar({ onNewChat, isCollapsed }: SidebarProps) {
  if (isCollapsed) return null;

  return (
    <motion.aside
      initial={{ x: -20, opacity: 0 }}
      animate={{ x: 0, opacity: 1 }}
      transition={{ duration: 0.3 }}
      className="w-64 h-full flex flex-col border-r border-border/50 bg-sidebar"
    >
      {/* Logo 区域 */}
      <div className="p-4 flex items-center gap-3">
        <div className="w-9 h-9 rounded-xl bg-primary/20 border border-primary/30 flex items-center justify-center">
          <Zap className="w-5 h-5 text-primary" />
        </div>
        <div>
          <h1 className="text-base font-semibold text-foreground tracking-tight">
            Manus
          </h1>
          <p className="text-[10px] text-muted-foreground -mt-0.5">AI Agent MVP</p>
        </div>
      </div>

      {/* 新对话按钮 */}
      <div className="px-3 mb-2">
        <Button
          onClick={onNewChat}
          variant="outline"
          className="w-full justify-start gap-2 h-9 text-sm bg-transparent border-border/50 hover:bg-accent/50"
        >
          <Plus className="w-4 h-4" />
          新对话
        </Button>
      </div>

      {/* 对话列表 */}
      <div className="flex-1 overflow-y-auto px-3 py-2">
        <p className="text-xs text-muted-foreground/50 px-2 mb-2 font-medium">
          最近对话
        </p>
        {/* 对话列表项会在有对话时显示 */}
        <div className="space-y-1">
          <div className="flex items-center gap-2 px-2 py-2 rounded-lg text-muted-foreground/40 text-xs">
            <MessageSquare className="w-3.5 h-3.5" />
            <span>暂无对话记录</span>
          </div>
        </div>
      </div>

      {/* 底部设置 */}
      <div className="p-3 border-t border-border/30">
        <button
          onClick={() => toast("设置功能即将上线", { description: "Feature coming soon" })}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors w-full"
        >
          <Settings className="w-4 h-4" />
          <span>设置</span>
        </button>
      </div>
    </motion.aside>
  );
}
