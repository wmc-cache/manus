/**
 * Sidebar - 对话列表侧边栏
 * 设计风格: 深色毛玻璃侧边栏
 */
import { useEffect, useState } from "react";
import { motion } from "framer-motion";
import {
  Plus,
  MessageSquare,
  Settings,
  Trash2,
  Zap,
} from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Switch } from "@/components/ui/switch";
import type { Conversation, DeepResearchSettingsData } from "@/types";
import { toast } from "sonner";

interface SidebarProps {
  onNewChat: () => void;
  conversations: Conversation[];
  activeConversationId: string | null;
  onSelectConversation?: (id: string) => void;
  onDeleteConversation?: (id: string) => void;
  deepResearchSettings: DeepResearchSettingsData;
  onDeepResearchSettingsChange: (next: DeepResearchSettingsData) => void;
  isCollapsed?: boolean;
}

function formatConversationMeta(conv: Conversation): string {
  const count = conv.messageCount ?? conv.messages.length;
  const date = conv.createdAt ? new Date(conv.createdAt) : null;
  if (!date || Number.isNaN(date.getTime())) {
    return `${count} 条消息`;
  }
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  return `${count} 条消息 · ${hh}:${mm}`;
}

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

export default function Sidebar({
  onNewChat,
  conversations,
  activeConversationId,
  onSelectConversation,
  onDeleteConversation,
  deepResearchSettings,
  onDeepResearchSettingsChange,
  isCollapsed,
}: SidebarProps) {
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [draftEnabledByDefault, setDraftEnabledByDefault] = useState(deepResearchSettings.enabledByDefault);
  const [draftConcurrency, setDraftConcurrency] = useState(String(deepResearchSettings.maxConcurrency));
  const [draftMaxItems, setDraftMaxItems] = useState(String(deepResearchSettings.maxItems));
  const [draftMaxIterations, setDraftMaxIterations] = useState(String(deepResearchSettings.maxIterations));

  useEffect(() => {
    if (settingsOpen) return;
    setDraftEnabledByDefault(deepResearchSettings.enabledByDefault);
    setDraftConcurrency(String(deepResearchSettings.maxConcurrency));
    setDraftMaxItems(String(deepResearchSettings.maxItems));
    setDraftMaxIterations(String(deepResearchSettings.maxIterations));
  }, [deepResearchSettings, settingsOpen]);

  const handleSaveSettings = () => {
    const next: DeepResearchSettingsData = {
      enabledByDefault: draftEnabledByDefault,
      maxConcurrency: clampInt(Number(draftConcurrency), 1, 20),
      maxItems: clampInt(Number(draftMaxItems), 1, 100),
      maxIterations: clampInt(Number(draftMaxIterations), 1, 12),
    };
    onDeepResearchSettingsChange(next);
    setSettingsOpen(false);
    toast("深度研究设置已保存");
  };

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
        {conversations.length === 0 ? (
          <div className="space-y-1">
            <div className="flex items-center gap-2 px-2 py-2 rounded-lg text-muted-foreground/40 text-xs">
              <MessageSquare className="w-3.5 h-3.5" />
              <span>暂无对话记录</span>
            </div>
          </div>
        ) : (
          <div className="space-y-1">
            {conversations.map((conv) => {
              const active = conv.id === activeConversationId;
              return (
                <div key={conv.id} className="group relative">
                  <button
                    onClick={() => onSelectConversation?.(conv.id)}
                    className={`w-full text-left px-2.5 py-2 pr-9 rounded-lg border transition-colors ${
                      active
                        ? "bg-primary/10 border-primary/30"
                        : "bg-transparent border-transparent hover:bg-accent/40"
                    }`}
                  >
                    <div className="flex items-center gap-2">
                      <MessageSquare className={`w-3.5 h-3.5 ${active ? "text-primary" : "text-muted-foreground/70"}`} />
                      <span className={`text-xs truncate ${active ? "text-foreground" : "text-foreground/90"}`}>
                        {conv.title || "新对话"}
                      </span>
                    </div>
                    <div className="text-[10px] mt-1 pl-[22px] text-muted-foreground/60">
                      {formatConversationMeta(conv)}
                    </div>
                  </button>
                  <button
                    type="button"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteConversation?.(conv.id);
                    }}
                    className={`absolute right-2 top-2 p-1 rounded-md transition-colors ${
                      active
                        ? "text-muted-foreground/90 hover:text-destructive hover:bg-destructive/10"
                        : "text-muted-foreground/0 group-hover:text-muted-foreground/80 hover:text-destructive hover:bg-destructive/10"
                    }`}
                    title="删除会话"
                    aria-label="删除会话"
                  >
                    <Trash2 className="w-3.5 h-3.5" />
                  </button>
                </div>
              );
            })}
          </div>
        )}
      </div>

      {/* 底部设置 */}
      <div className="p-3 border-t border-border/30">
        <button
          onClick={() => setSettingsOpen(true)}
          className="flex items-center gap-2 px-2 py-2 rounded-lg text-sm text-muted-foreground hover:text-foreground hover:bg-accent/50 transition-colors w-full"
        >
          <Settings className="w-4 h-4" />
          <span>设置</span>
        </button>
      </div>

      <Dialog open={settingsOpen} onOpenChange={setSettingsOpen}>
        <DialogContent className="max-w-md bg-background/95 border-border/50">
          <DialogHeader>
            <DialogTitle>深度研究设置</DialogTitle>
            <DialogDescription>
              配置勾选“深度研究”时子代理并行参数。
            </DialogDescription>
          </DialogHeader>

          <div className="space-y-4 text-sm">
            <label className="flex items-center justify-between gap-3">
              <span>默认开启深度研究</span>
              <Switch
                checked={draftEnabledByDefault}
                onCheckedChange={setDraftEnabledByDefault}
              />
            </label>

            <label className="space-y-1 block">
              <span className="text-xs text-muted-foreground">子代理并发数 (1-20)</span>
              <Input
                type="number"
                min={1}
                max={20}
                value={draftConcurrency}
                onChange={(e) => setDraftConcurrency(e.target.value)}
              />
            </label>

            <label className="space-y-1 block">
              <span className="text-xs text-muted-foreground">单次最大条目数 (1-100)</span>
              <Input
                type="number"
                min={1}
                max={100}
                value={draftMaxItems}
                onChange={(e) => setDraftMaxItems(e.target.value)}
              />
            </label>

            <label className="space-y-1 block">
              <span className="text-xs text-muted-foreground">单个子代理最大迭代 (1-12)</span>
              <Input
                type="number"
                min={1}
                max={12}
                value={draftMaxIterations}
                onChange={(e) => setDraftMaxIterations(e.target.value)}
              />
            </label>
          </div>

          <DialogFooter>
            <Button
              variant="outline"
              onClick={() => setSettingsOpen(false)}
            >
              取消
            </Button>
            <Button onClick={handleSaveSettings}>
              保存
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </motion.aside>
  );
}
