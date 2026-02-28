/**
 * Home - Manus MVP 主页面（含计算机窗口 + 会话隔离）
 * 设计风格: Glass Workspace 毛玻璃工作台
 * 布局: 左侧侧边栏 + 中间对话区 + 右侧计算机窗口
 * 
 * 色彩: 深灰蓝底色 + 毛玻璃卡片 + 蓝紫色强调色
 * 字体: DM Sans (UI) + JetBrains Mono (代码)
 * 动画: Spring 物理动画，卡片悬停上浮
 */
import { useRef, useEffect, useState, useCallback } from "react";
import { AnimatePresence, motion } from "framer-motion";
import { PanelLeftClose, PanelLeft, Monitor, Maximize2 } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { useAgent } from "@/hooks/useAgent";
import { useSandbox } from "@/hooks/useSandbox";
import Sidebar from "@/components/Sidebar";
import EmptyState from "@/components/EmptyState";
import MessageBubble from "@/components/MessageBubble";
import ThinkingIndicator from "@/components/ThinkingIndicator";
import ChatInput from "@/components/ChatInput";
import ComputerPanel from "@/components/sandbox/ComputerPanel";
import type { ChatImagePayload, DeepResearchSettingsData, SubAgentSessionDetailData } from "@/types";
import { toast } from "sonner";

const PLAN_STATUS_LABEL: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
};

const PLAN_REASON_LABEL: Record<string, string> = {
  initialized: "已生成计划",
  resumed: "继续执行",
  executing: "进入执行",
  phase_advanced: "推进下一阶段",
  finalizing: "进入总结阶段",
  completed: "任务完成",
  paused_manual_takeover: "手动接管暂停",
  failed_invalid_args: "参数错误中断",
  finalizing_tool_loop: "循环后总结",
  completed_tool_loop: "循环后完成",
  limit_reached: "达到轮数上限",
};

const PLAN_SOURCE_LABEL: Record<string, string> = {
  llm: "模型计划",
  template: "模板计划",
};

const SUB_AGENT_STATUS_LABEL: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  completed_with_limit: "已完成(达上限)",
  failed: "失败",
  max_iterations: "达上限",
};

const DEEP_RESEARCH_SETTINGS_KEY = "manus.deep_research.settings";
const DEFAULT_DEEP_RESEARCH_SETTINGS: DeepResearchSettingsData = {
  enabledByDefault: false,
  maxConcurrency: 3,
  maxItems: 20,
  maxIterations: 4,
};
const PREVIEW_SCALE = 0.6;

function clampInt(value: number, min: number, max: number): number {
  if (!Number.isFinite(value)) return min;
  return Math.min(max, Math.max(min, Math.round(value)));
}

function loadDeepResearchSettings(): DeepResearchSettingsData {
  try {
    const raw = window.localStorage.getItem(DEEP_RESEARCH_SETTINGS_KEY);
    if (!raw) return DEFAULT_DEEP_RESEARCH_SETTINGS;
    const parsed = JSON.parse(raw) as Partial<DeepResearchSettingsData>;
    return {
      enabledByDefault: Boolean(parsed.enabledByDefault),
      maxConcurrency: clampInt(Number(parsed.maxConcurrency), 1, 20),
      maxItems: clampInt(Number(parsed.maxItems), 1, 100),
      maxIterations: clampInt(Number(parsed.maxIterations), 1, 12),
    };
  } catch {
    return DEFAULT_DEEP_RESEARCH_SETTINGS;
  }
}

const HERO_BG =
  "https://private-us-east-1.manuscdn.com/sessionFile/wYRFO7o4twJWKVfWlISpqY/sandbox/drOIOkPpOFG7RUVTquZoNi-img-1_1771589325000_na1fn_aGVyby1iZw.png?x-oss-process=image/resize,w_1920,h_1920/format,webp/quality,q_80&Expires=1798761600&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvd1lSRk83bzR0d0pXS1ZmV2xJU3BxWS9zYW5kYm94L2RyT0lPa1BwT0ZHN1JVVlRxdVpvTmktaW1nLTFfMTc3MTU4OTMyNTAwMF9uYTFmbl9hR1Z5YnkxaVp3LnBuZz94LW9zcy1wcm9jZXNzPWltYWdlL3Jlc2l6ZSx3XzE5MjAsaF8xOTIwL2Zvcm1hdCx3ZWJwL3F1YWxpdHkscV84MCIsIkNvbmRpdGlvbiI6eyJEYXRlTGVzc1RoYW4iOnsiQVdTOkVwb2NoVGltZSI6MTc5ODc2MTYwMH19fV19&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=bLV0gYsEmcPgJBG-cXYD2csT3PZ-duS39GcAI3N41yZoT3xOBgXHuzp2BCO2vC8YHdisx~Z11Ihq5Y6e51f2wUIpYhtvEG73mP92xhMxX85lMa~73jqZiqTwagT3gOc2iEtU9l9vbUfrNNWZcgt6KesNAhYIaKGk0dxlEkS4ZnSqHeM~sPF~KntQvY3rprWr51kL-qPnqXn1rWgCbYkgU0tdhSN0oFu2HBnygMIGWtPpmfj5l0Ts0WrD~UmBURNrVIYw8ECC-WRACa84M3G75csooYLAW5F8JpRviklNLneu9iW3oLUUUbQcJqKM57E08UicyQ~SoeVTkaUZGSxIUg__";

export default function Home() {
  const {
    conversations,
    messages,
    isLoading,
    isThinking,
    thinkingStatus,
    error,
    iteration,
    limitReached,
    continueMessage,
    plan,
    planSource,
    planReason,
    todoPath,
    subAgentIndex,
    currentToolCall,
    conversationId,
    sendMessage,
    loadConversation,
    loadSubAgentSession,
    createConversation,
    continueAgent,
    deleteConversation,
    stopAgent,
  } = useAgent();

  const sandbox = useSandbox();

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [computerOpen, setComputerOpen] = useState(false);
  const [deepResearchSettings, setDeepResearchSettings] = useState<DeepResearchSettingsData>(DEFAULT_DEEP_RESEARCH_SETTINGS);
  const [subAgentDialogOpen, setSubAgentDialogOpen] = useState(false);
  const [subAgentSessionLoading, setSubAgentSessionLoading] = useState(false);
  const [subAgentSessionError, setSubAgentSessionError] = useState<string | null>(null);
  const [activeSubAgentSession, setActiveSubAgentSession] = useState<SubAgentSessionDetailData | null>(null);
  const [previewDragConstraints, setPreviewDragConstraints] = useState({
    top: -64,
    left: -1200,
    right: 16,
    bottom: 800,
  });
  const layoutRef = useRef<HTMLDivElement>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  const previewDragBlockedRef = useRef(false);
  const previewDragResetTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  // 记录上一次的 conversationId，用于检测变化
  const prevConvIdRef = useRef<string | null>(null);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  useEffect(() => {
    setDeepResearchSettings(loadDeepResearchSettings());
  }, []);

  useEffect(() => {
    try {
      window.localStorage.setItem(DEEP_RESEARCH_SETTINGS_KEY, JSON.stringify(deepResearchSettings));
    } catch {
      // ignore storage failures
    }
  }, [deepResearchSettings]);

  // 当 conversationId 变化时，切换计算机窗口的订阅
  useEffect(() => {
    if (conversationId !== prevConvIdRef.current) {
      sandbox.switchConversation(conversationId || null);
      prevConvIdRef.current = conversationId;
    }
  }, [conversationId, sandbox.switchConversation]);

  useEffect(() => {
    setSubAgentDialogOpen(false);
    setSubAgentSessionLoading(false);
    setSubAgentSessionError(null);
    setActiveSubAgentSession(null);
  }, [conversationId]);

  useEffect(() => {
    const recalcPreviewDragConstraints = () => {
      const el = layoutRef.current;
      if (!el) return;
      const containerWidth = el.clientWidth;
      const containerHeight = el.clientHeight;
      const isSm = window.matchMedia("(min-width: 640px)").matches;
      const previewWidth = isSm ? 330 : 300;
      const previewHeight = isSm ? 220 : 200;
      const initialRight = 16;
      const initialTop = 64;
      const initialLeft = Math.max(0, containerWidth - previewWidth - initialRight);

      setPreviewDragConstraints({
        top: -initialTop,
        left: -initialLeft,
        right: Math.max(0, containerWidth - previewWidth - initialLeft),
        bottom: Math.max(0, containerHeight - previewHeight - initialTop),
      });
    };

    recalcPreviewDragConstraints();
    window.addEventListener("resize", recalcPreviewDragConstraints);
    return () => {
      window.removeEventListener("resize", recalcPreviewDragConstraints);
    };
  }, [sidebarOpen, computerOpen]);

  useEffect(() => {
    return () => {
      if (previewDragResetTimerRef.current) {
        clearTimeout(previewDragResetTimerRef.current);
      }
    };
  }, []);

  const handleNewChat = useCallback(() => {
    void createConversation();
  }, [createConversation]);

  const handleSendMessage = useCallback((text: string, options?: { deepResearch?: boolean; images?: ChatImagePayload[] }) => {
    if (options?.deepResearch) {
      sendMessage(text, {
        images: options.images,
        deepResearch: {
          enabled: true,
          maxConcurrency: deepResearchSettings.maxConcurrency,
          maxItems: deepResearchSettings.maxItems,
          maxIterations: deepResearchSettings.maxIterations,
        },
      });
      return;
    }
    sendMessage(text, { images: options?.images });
  }, [deepResearchSettings, sendMessage]);

  const handleSuggestionClick = useCallback(
    (text: string) => {
      handleSendMessage(text, { deepResearch: deepResearchSettings.enabledByDefault });
    },
    [deepResearchSettings.enabledByDefault, handleSendMessage]
  );

  const handleSelectConversation = useCallback(
    async (id: string) => {
      if (!id || id === conversationId || isLoading) return;
      await loadConversation(id);
    },
    [conversationId, isLoading, loadConversation]
  );

  const handleDeleteConversation = useCallback(
    async (id: string) => {
      if (!id) return;

      const target = conversations.find((conv) => conv.id === id);
      const title = target?.title?.trim() || "新对话";
      const confirmed = window.confirm(`确认删除会话「${title}」？此操作不可恢复。`);
      if (!confirmed) return;

      const ok = await deleteConversation(id);
      if (ok) {
        toast("会话已删除");
      } else {
        toast("删除失败", { description: "请稍后重试。" });
      }
    },
    [conversations, deleteConversation]
  );

  const handleDeepResearchSettingsChange = useCallback((next: DeepResearchSettingsData) => {
    setDeepResearchSettings({
      enabledByDefault: Boolean(next.enabledByDefault),
      maxConcurrency: clampInt(next.maxConcurrency, 1, 20),
      maxItems: clampInt(next.maxItems, 1, 100),
      maxIterations: clampInt(next.maxIterations, 1, 12),
    });
  }, []);

  const handleOpenSubAgentSession = useCallback(async (sessionId: string) => {
    if (!conversationId || !sessionId) return;

    setSubAgentDialogOpen(true);
    setSubAgentSessionLoading(true);
    setSubAgentSessionError(null);
    setActiveSubAgentSession(null);

    const detail = await loadSubAgentSession(conversationId, sessionId);
    if (!detail) {
      setSubAgentSessionError("子代理会话加载失败，请稍后重试。");
      setSubAgentSessionLoading(false);
      return;
    }

    setActiveSubAgentSession(detail);
    setSubAgentSessionLoading(false);
  }, [conversationId, loadSubAgentSession]);

  const hasMessages = messages.length > 0;
  const manualTakeoverActive = sandbox.manualTakeoverEnabled;
  const handleOpenComputerPanel = useCallback(() => {
    if (previewDragBlockedRef.current) return;
    setComputerOpen(true);
  }, []);

  const handlePreviewDragStart = useCallback(() => {
    previewDragBlockedRef.current = true;
    if (previewDragResetTimerRef.current) {
      clearTimeout(previewDragResetTimerRef.current);
    }
  }, []);

  const handlePreviewDragEnd = useCallback(() => {
    if (previewDragResetTimerRef.current) {
      clearTimeout(previewDragResetTimerRef.current);
    }
    previewDragResetTimerRef.current = setTimeout(() => {
      previewDragBlockedRef.current = false;
    }, 120);
  }, []);

  return (
    <div
      className="h-screen flex overflow-hidden"
      style={{
        backgroundImage: `url(${HERO_BG})`,
        backgroundSize: "cover",
        backgroundPosition: "center",
      }}
    >
      {/* 背景遮罩 */}
      <div className="absolute inset-0 bg-background/85 backdrop-blur-sm" />

      {/* 内容层 */}
      <div ref={layoutRef} className="relative flex w-full h-full">
        {/* 侧边栏 */}
        <AnimatePresence>
          {sidebarOpen && (
            <Sidebar
              onNewChat={handleNewChat}
              conversations={conversations}
              activeConversationId={conversationId}
              onSelectConversation={handleSelectConversation}
              onDeleteConversation={handleDeleteConversation}
              deepResearchSettings={deepResearchSettings}
              onDeepResearchSettingsChange={handleDeepResearchSettingsChange}
            />
          )}
        </AnimatePresence>

        {/* 主内容区 - 对话 */}
        <main className="relative flex-1 flex flex-col h-full min-w-0">
          {/* 顶部栏 */}
          <header className="flex items-center justify-between px-4 py-3 border-b border-border/30">
            <div className="flex items-center gap-2">
              <Button
                variant="ghost"
                size="icon"
                className="h-8 w-8 text-muted-foreground hover:text-foreground"
                onClick={() => setSidebarOpen(!sidebarOpen)}
              >
                {sidebarOpen ? (
                  <PanelLeftClose className="w-4 h-4" />
                ) : (
                  <PanelLeft className="w-4 h-4" />
                )}
              </Button>

              {hasMessages && (
                <div className="flex items-center gap-2">
                  <div
                    className={`w-2 h-2 rounded-full animate-pulse ${
                      manualTakeoverActive ? "bg-amber-400" : "bg-emerald-400"
                    }`}
                  />
                  <span className="text-sm text-muted-foreground">
                    {manualTakeoverActive
                      ? "手动接管中"
                      : (isLoading ? "Agent 工作中..." : "就绪")}
                  </span>
                </div>
              )}
            </div>

            <div className="hidden sm:flex items-center gap-2 text-xs text-muted-foreground">
              <Monitor className="w-3.5 h-3.5" />
              <span>计算机预览</span>
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  sandbox.connected ? "bg-emerald-400" : "bg-red-400"
                }`}
              />
            </div>
          </header>

          {!computerOpen && (
            <motion.div
              drag
              dragConstraints={previewDragConstraints}
              dragMomentum={false}
              dragElastic={0.06}
              onDragStart={handlePreviewDragStart}
              onDragEnd={handlePreviewDragEnd}
              onClick={handleOpenComputerPanel}
              className="absolute right-4 top-16 z-20 h-[200px] w-[300px] sm:h-[220px] sm:w-[330px] overflow-hidden rounded-xl border border-border/30 bg-background/40 shadow-[0_12px_30px_rgba(0,0,0,0.35)] cursor-grab active:cursor-grabbing select-none touch-none"
              title="拖拽移动预览，点击打开计算机面板"
              aria-label="拖拽移动或打开计算机面板"
            >
              <div
                className="pointer-events-none absolute left-0 top-0"
                style={{
                  transform: `scale(${PREVIEW_SCALE})`,
                  transformOrigin: "top left",
                  width: `calc(100% / ${PREVIEW_SCALE})`,
                  height: `calc(100% / ${PREVIEW_SCALE})`,
                }}
              >
                <ComputerPanel
                  connected={sandbox.connected}
                  activeWindow={sandbox.activeWindow}
                  onWindowChange={sandbox.setActiveWindow}
                  terminalOutput={sandbox.terminalOutput}
                  onTerminalInput={sandbox.sendTerminalInput}
                  browserData={sandbox.browserData}
                  editorFile={sandbox.editorFile}
                  fileTree={sandbox.fileTree}
                  onFileClick={sandbox.fetchFileContent}
                  onRefreshFiles={sandbox.fetchFileTree}
                  onDownloadAllFiles={sandbox.downloadAllFiles}
                  downloadingAllFiles={sandbox.downloadingAllFiles}
                  manualTakeoverEnabled={sandbox.manualTakeoverEnabled}
                  manualTakeoverTarget={sandbox.manualTakeoverTarget}
                  onToggleManualTakeover={sandbox.setManualTakeover}
                  onBrowserClick={sandbox.browserClick}
                  onBrowserType={sandbox.browserType}
                  onBrowserNavigate={sandbox.browserNavigate}
                  onBrowserScroll={sandbox.browserScroll}
                  onBrowserKey={sandbox.browserKey}
                  browserInteractionError={sandbox.browserInteractionError}
                  currentTool={currentToolCall ? { name: currentToolCall.name, arguments: currentToolCall.arguments } : null}
                  isAgentWorking={isLoading}
                />
              </div>

              <div className="absolute inset-0 bg-gradient-to-b from-transparent to-black/18 pointer-events-none" />
              <Button
                variant="ghost"
                size="icon"
                className="absolute right-2 top-2 h-6 w-6 bg-black/35 text-white/80 hover:bg-black/55 hover:text-white"
                onPointerDown={(e) => e.stopPropagation()}
                onClick={(e) => {
                  e.stopPropagation();
                  setComputerOpen(true);
                }}
                title="展开计算机面板"
              >
                <Maximize2 className="w-3.5 h-3.5" />
              </Button>
            </motion.div>
          )}

          {/* 消息区域 */}
          <div className="flex-1 overflow-y-auto">
            {!hasMessages ? (
              <EmptyState onSuggestionClick={handleSuggestionClick} />
            ) : (
              <div className="max-w-3xl mx-auto px-4 py-6">
                {plan && (
                  <div className="mx-4 mb-3 rounded-xl border border-border/30 bg-background/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">执行计划</p>
                      <div className="flex items-center gap-2 text-[11px] text-muted-foreground/80">
                        {planSource && (
                          <span>{PLAN_SOURCE_LABEL[planSource] || planSource}</span>
                        )}
                        {planReason && (
                          <span>{PLAN_REASON_LABEL[planReason] || planReason}</span>
                        )}
                      </div>
                    </div>
                    <p className="mt-1 text-sm text-foreground/95">{plan.goal || "当前任务"}</p>
                    <div className="mt-2 space-y-1">
                      {plan.phases.map((phase) => {
                        const status = PLAN_STATUS_LABEL[phase.status] || phase.status;
                        const isRunning = phase.status === "running";
                        return (
                          <div
                            key={phase.id}
                            className={`flex items-center justify-between rounded-md border px-2 py-1 text-xs ${
                              isRunning
                                ? "border-primary/40 bg-primary/10 text-primary"
                                : "border-border/30 bg-background/30 text-muted-foreground"
                            }`}
                          >
                            <span>{phase.id}. {phase.title}</span>
                            <span>{status}</span>
                          </div>
                        );
                      })}
                    </div>
                    {todoPath && (
                      <p className="mt-2 text-[11px] text-muted-foreground/80 font-mono break-all">
                        {todoPath}
                      </p>
                    )}
                  </div>
                )}

                {subAgentIndex && subAgentIndex.sub_sessions.length > 0 && (
                  <div className="mx-4 mb-3 rounded-xl border border-border/30 bg-background/40 p-3">
                    <div className="flex items-center justify-between gap-2">
                      <p className="text-xs text-muted-foreground">子代理运行树</p>
                      <span className="text-[11px] text-muted-foreground/80">
                        {subAgentIndex.sub_sessions.length} agents
                      </span>
                    </div>
                    <p className="mt-1 text-[11px] font-mono text-muted-foreground/90 break-all">
                      run: {subAgentIndex.run_id}
                    </p>
                    {subAgentIndex.reduce_goal && (
                      <p className="mt-1 text-xs text-foreground/90">
                        reduce: {subAgentIndex.reduce_goal}
                      </p>
                    )}
                    {subAgentIndex.limits && (
                      <p className="mt-1 text-[11px] text-muted-foreground/80">
                        limits: c={subAgentIndex.limits.max_concurrency ?? "-"}, items={subAgentIndex.limits.max_items ?? "-"}, iter={subAgentIndex.limits.max_iterations ?? "-"}
                      </p>
                    )}
                    <div className="mt-2 space-y-1">
                      {subAgentIndex.sub_sessions.map((session) => {
                        const status = SUB_AGENT_STATUS_LABEL[session.status] || session.status;
                        const isRunning = session.status === "running";
                        const isFailed = session.status === "failed";
                        const statusClass = isRunning
                          ? "border-primary/40 bg-primary/10 text-primary"
                          : isFailed
                            ? "border-destructive/40 bg-destructive/10 text-destructive"
                            : "border-border/30 bg-background/30 text-muted-foreground";
                        return (
                          <button
                            type="button"
                            key={session.session_id}
                            className={`w-full rounded-md border px-2 py-1 text-left text-xs transition-colors hover:bg-background/50 ${statusClass}`}
                            onClick={() => {
                              void handleOpenSubAgentSession(session.session_id);
                            }}
                          >
                            <div className="flex items-center justify-between gap-2">
                              <span>{session.item} ({session.agent_id})</span>
                              <span>{status}</span>
                            </div>
                            <p className="mt-1 font-mono text-[11px] break-all opacity-80">
                              {session.session_path}
                            </p>
                          </button>
                        );
                      })}
                    </div>
                    <p className="mt-2 text-[11px] text-muted-foreground/70">
                      点击条目可查看子代理会话轨迹
                    </p>
                    {subAgentIndex.reduce_summary_path && (
                      <p className="mt-2 text-[11px] font-mono text-muted-foreground/80 break-all">
                        summary: {subAgentIndex.reduce_summary_path}
                      </p>
                    )}
                  </div>
                )}

                {messages.map((msg) => (
                  <MessageBubble key={msg.id} message={msg} />
                ))}

                <AnimatePresence>
                  {isThinking && <ThinkingIndicator iteration={iteration} status={thinkingStatus} />}
                </AnimatePresence>

                {error && (
                  <div className="mx-4 my-2 p-3 rounded-xl bg-destructive/10 border border-destructive/20 text-sm text-destructive">
                    {error}
                  </div>
                )}

                {limitReached && continueMessage && (
                  <div className="mx-4 my-2 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-sm text-amber-300">
                    {continueMessage}
                  </div>
                )}

                {sandbox.manualBlockedReason && (
                  <div className="mx-4 my-2 p-3 rounded-xl bg-amber-500/10 border border-amber-500/20 text-sm text-amber-200">
                    {sandbox.manualBlockedReason}
                  </div>
                )}

                <div ref={messagesEndRef} />
              </div>
            )}
          </div>

          {/* 输入区域 */}
          {manualTakeoverActive && (
            <div className="mx-auto w-full max-w-3xl px-4">
              <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 px-3 py-2 text-xs text-amber-200">
                当前为手动接管模式，Agent 自动工具调用会暂停。完成后请在计算机窗口点击“接管中”释放接管，再继续让 Agent 执行。
              </div>
            </div>
          )}
          <ChatInput
            onSend={handleSendMessage}
            onContinue={continueAgent}
            onStop={stopAgent}
            isLoading={isLoading}
            showContinue={limitReached}
            continueLabel="继续"
            defaultDeepResearchEnabled={deepResearchSettings.enabledByDefault}
          />

          <Dialog
            open={subAgentDialogOpen}
            onOpenChange={(open) => {
              setSubAgentDialogOpen(open);
            }}
          >
            <DialogContent className="max-w-3xl bg-background/95 border-border/50">
              <DialogHeader>
                <DialogTitle>子代理会话详情</DialogTitle>
                <DialogDescription>
                  {activeSubAgentSession
                    ? `${activeSubAgentSession.item} (${activeSubAgentSession.agent_id})`
                    : "查看单个子代理的执行轨迹与结果"}
                </DialogDescription>
              </DialogHeader>

              <div className="max-h-[65vh] space-y-3 overflow-y-auto pr-1 text-sm">
                {subAgentSessionLoading && (
                  <div className="rounded-md border border-border/40 bg-background/40 p-3 text-muted-foreground">
                    正在加载子代理会话...
                  </div>
                )}

                {!subAgentSessionLoading && subAgentSessionError && (
                  <div className="rounded-md border border-destructive/40 bg-destructive/10 p-3 text-destructive">
                    {subAgentSessionError}
                  </div>
                )}

                {!subAgentSessionLoading && !subAgentSessionError && activeSubAgentSession && (
                  <>
                    <div className="rounded-md border border-border/40 bg-background/30 p-3 space-y-1 text-xs">
                      <p><span className="text-muted-foreground">status:</span> {activeSubAgentSession.status}</p>
                      <p><span className="text-muted-foreground">iterations:</span> {activeSubAgentSession.iterations}</p>
                      <p><span className="text-muted-foreground">session:</span> <span className="font-mono">{activeSubAgentSession.id}</span></p>
                      {activeSubAgentSession.workspace && (
                        <p><span className="text-muted-foreground">workspace:</span> <span className="font-mono break-all">{activeSubAgentSession.workspace}</span></p>
                      )}
                      <p><span className="text-muted-foreground">created:</span> {activeSubAgentSession.created_at}</p>
                    </div>

                    <div className="rounded-md border border-border/40 bg-background/30 p-3">
                      <p className="mb-2 text-xs text-muted-foreground">Final Answer</p>
                      <pre className="whitespace-pre-wrap break-words text-sm font-sans">
                        {activeSubAgentSession.final_answer || "(empty)"}
                      </pre>
                    </div>

                    {activeSubAgentSession.tool_steps.length > 0 && (
                      <div className="rounded-md border border-border/40 bg-background/30 p-3">
                        <p className="mb-2 text-xs text-muted-foreground">Tool Steps</p>
                        <div className="space-y-2">
                          {activeSubAgentSession.tool_steps.map((step, idx) => (
                            <div key={`${step.step ?? idx}-${step.tool ?? "tool"}`} className="rounded border border-border/30 px-2 py-1 text-xs">
                              <p>
                                step {step.step ?? idx + 1} | {step.tool || "(unknown)"}
                              </p>
                              {step.status && <p className="text-muted-foreground">status: {step.status}</p>}
                              {step.result_preview && (
                                <p className="mt-1 whitespace-pre-wrap break-words text-muted-foreground">
                                  {step.result_preview}
                                </p>
                              )}
                              {step.error && (
                                <p className="mt-1 whitespace-pre-wrap break-words text-destructive">
                                  {step.error}
                                </p>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}

                    {activeSubAgentSession.messages.length > 0 && (
                      <div className="rounded-md border border-border/40 bg-background/30 p-3">
                        <p className="mb-2 text-xs text-muted-foreground">Message Trace</p>
                        <div className="space-y-2">
                          {activeSubAgentSession.messages.map((msg, idx) => (
                            <div key={`${msg.role}-${idx}`} className="rounded border border-border/30 px-2 py-1">
                              <p className="text-xs text-muted-foreground">{msg.role}</p>
                              {msg.content && (
                                <p className="mt-1 whitespace-pre-wrap break-words text-xs">
                                  {msg.content}
                                </p>
                              )}
                              {msg.tool_calls && msg.tool_calls.length > 0 && (
                                <div className="mt-1 space-y-1">
                                  {msg.tool_calls.map((tc) => (
                                    <p key={tc.id} className="font-mono text-[11px] text-muted-foreground">
                                      {tc.function.name}({tc.function.arguments})
                                    </p>
                                  ))}
                                </div>
                              )}
                            </div>
                          ))}
                        </div>
                      </div>
                    )}
                  </>
                )}
              </div>
            </DialogContent>
          </Dialog>
        </main>

        {/* 计算机窗口面板（预览点击后展开为右侧分栏） */}
        <AnimatePresence>
          {computerOpen && (
            <motion.div
              initial={{ width: 0, opacity: 0 }}
              animate={{ width: "50%", opacity: 1 }}
              exit={{ width: 0, opacity: 0 }}
              transition={{ type: "spring", stiffness: 300, damping: 30 }}
              className="h-full border-l border-border/30 overflow-hidden"
              style={{ maxWidth: "700px", minWidth: "380px" }}
            >
              <ComputerPanel
                connected={sandbox.connected}
                activeWindow={sandbox.activeWindow}
                onWindowChange={sandbox.setActiveWindow}
                terminalOutput={sandbox.terminalOutput}
                onTerminalInput={sandbox.sendTerminalInput}
                browserData={sandbox.browserData}
                editorFile={sandbox.editorFile}
                fileTree={sandbox.fileTree}
                onFileClick={sandbox.fetchFileContent}
                onRefreshFiles={sandbox.fetchFileTree}
                onDownloadAllFiles={sandbox.downloadAllFiles}
                downloadingAllFiles={sandbox.downloadingAllFiles}
                manualTakeoverEnabled={sandbox.manualTakeoverEnabled}
                manualTakeoverTarget={sandbox.manualTakeoverTarget}
                onToggleManualTakeover={sandbox.setManualTakeover}
                onBrowserClick={sandbox.browserClick}
                onBrowserType={sandbox.browserType}
                onBrowserNavigate={sandbox.browserNavigate}
                onBrowserScroll={sandbox.browserScroll}
                onBrowserKey={sandbox.browserKey}
                browserInteractionError={sandbox.browserInteractionError}
                onClose={() => setComputerOpen(false)}
                currentTool={currentToolCall ? { name: currentToolCall.name, arguments: currentToolCall.arguments } : null}
                isAgentWorking={isLoading}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
