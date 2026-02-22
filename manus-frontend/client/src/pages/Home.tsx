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
import { PanelLeftClose, PanelLeft, Monitor, PanelRightClose } from "lucide-react";
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
import type { SubAgentSessionDetailData } from "@/types";
import { toast } from "sonner";

const PLAN_STATUS_LABEL: Record<string, string> = {
  pending: "待执行",
  running: "执行中",
  completed: "已完成",
  failed: "失败",
};

const SUB_AGENT_STATUS_LABEL: Record<string, string> = {
  running: "执行中",
  completed: "已完成",
  completed_with_limit: "已完成(达上限)",
  failed: "失败",
  max_iterations: "达上限",
};

const HERO_BG =
  "https://private-us-east-1.manuscdn.com/sessionFile/wYRFO7o4twJWKVfWlISpqY/sandbox/drOIOkPpOFG7RUVTquZoNi-img-1_1771589325000_na1fn_aGVyby1iZw.png?x-oss-process=image/resize,w_1920,h_1920/format,webp/quality,q_80&Expires=1798761600&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvd1lSRk83bzR0d0pXS1ZmV2xJU3BxWS9zYW5kYm94L2RyT0lPa1BwT0ZHN1JVVlRxdVpvTmktaW1nLTFfMTc3MTU4OTMyNTAwMF9uYTFmbl9hR1Z5YnkxaVp3LnBuZz94LW9zcy1wcm9jZXNzPWltYWdlL3Jlc2l6ZSx3XzE5MjAsaF8xOTIwL2Zvcm1hdCx3ZWJwL3F1YWxpdHkscV84MCIsIkNvbmRpdGlvbiI6eyJEYXRlTGVzc1RoYW4iOnsiQVdTOkVwb2NoVGltZSI6MTc5ODc2MTYwMH19fV19&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=bLV0gYsEmcPgJBG-cXYD2csT3PZ-duS39GcAI3N41yZoT3xOBgXHuzp2BCO2vC8YHdisx~Z11Ihq5Y6e51f2wUIpYhtvEG73mP92xhMxX85lMa~73jqZiqTwagT3gOc2iEtU9l9vbUfrNNWZcgt6KesNAhYIaKGk0dxlEkS4ZnSqHeM~sPF~KntQvY3rprWr51kL-qPnqXn1rWgCbYkgU0tdhSN0oFu2HBnygMIGWtPpmfj5l0Ts0WrD~UmBURNrVIYw8ECC-WRACa84M3G75csooYLAW5F8JpRviklNLneu9iW3oLUUUbQcJqKM57E08UicyQ~SoeVTkaUZGSxIUg__";

export default function Home() {
  const {
    conversations,
    messages,
    isLoading,
    isThinking,
    error,
    iteration,
    limitReached,
    continueMessage,
    plan,
    planReason,
    todoPath,
    subAgentIndex,
    conversationId,
    sendMessage,
    loadConversation,
    loadSubAgentSession,
    continueAgent,
    deleteConversation,
    stopAgent,
    clearMessages,
  } = useAgent();

  const sandbox = useSandbox();

  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [computerOpen, setComputerOpen] = useState(true);
  const [subAgentDialogOpen, setSubAgentDialogOpen] = useState(false);
  const [subAgentSessionLoading, setSubAgentSessionLoading] = useState(false);
  const [subAgentSessionError, setSubAgentSessionError] = useState<string | null>(null);
  const [activeSubAgentSession, setActiveSubAgentSession] = useState<SubAgentSessionDetailData | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);
  // 记录上一次的 conversationId，用于检测变化
  const prevConvIdRef = useRef<string | null>(null);

  // 自动滚动到底部
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages, isThinking]);

  // 当 Agent 开始工作时自动打开计算机窗口
  useEffect(() => {
    if (isLoading) {
      setComputerOpen(true);
    }
  }, [isLoading]);

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

  const handleNewChat = useCallback(() => {
    clearMessages();
    // 新对话时重置计算机窗口
    sandbox.switchConversation(null);
    prevConvIdRef.current = null;
  }, [clearMessages, sandbox.switchConversation]);

  const handleSuggestionClick = useCallback(
    (text: string) => {
      sendMessage(text);
    },
    [sendMessage]
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
      <div className="relative flex w-full h-full">
        {/* 侧边栏 */}
        <AnimatePresence>
          {sidebarOpen && (
            <Sidebar
              onNewChat={handleNewChat}
              conversations={conversations}
              activeConversationId={conversationId}
              onSelectConversation={handleSelectConversation}
              onDeleteConversation={handleDeleteConversation}
            />
          )}
        </AnimatePresence>

        {/* 主内容区 - 对话 */}
        <main className="flex-1 flex flex-col h-full min-w-0">
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

            {/* 计算机窗口切换按钮 */}
            <Button
              variant="ghost"
              size="sm"
              className={`gap-1.5 text-xs ${
                computerOpen
                  ? "text-primary"
                  : "text-muted-foreground hover:text-foreground"
              }`}
              onClick={() => setComputerOpen(!computerOpen)}
            >
              {computerOpen ? (
                <PanelRightClose className="w-4 h-4" />
              ) : (
                <Monitor className="w-4 h-4" />
              )}
              <span className="hidden sm:inline">
                {computerOpen ? "收起" : "计算机"}
              </span>
              {/* 连接状态点 */}
              <div
                className={`w-1.5 h-1.5 rounded-full ${
                  sandbox.connected ? "bg-emerald-400" : "bg-red-400"
                }`}
              />
            </Button>
          </header>

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
                      {planReason && (
                        <span className="text-[11px] text-muted-foreground/80">{planReason}</span>
                      )}
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
                  {isThinking && <ThinkingIndicator iteration={iteration} />}
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
                当前为手动接管模式，Agent 自动工具调用会暂停。完成后请在右侧点击“接管中”释放接管，再继续让 Agent 执行。
              </div>
            </div>
          )}
          <ChatInput
            onSend={sendMessage}
            onContinue={continueAgent}
            onStop={stopAgent}
            isLoading={isLoading}
            showContinue={limitReached}
            continueLabel="继续"
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

        {/* 计算机窗口面板 */}
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
                manualTakeoverEnabled={sandbox.manualTakeoverEnabled}
                manualTakeoverTarget={sandbox.manualTakeoverTarget}
                onToggleManualTakeover={sandbox.setManualTakeover}
                onBrowserClick={sandbox.browserClick}
                onBrowserType={sandbox.browserType}
                onBrowserScroll={sandbox.browserScroll}
                onBrowserKey={sandbox.browserKey}
                browserInteractionError={sandbox.browserInteractionError}
                onClose={() => setComputerOpen(false)}
              />
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
