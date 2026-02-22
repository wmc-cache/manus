import { useState, useCallback, useRef, useEffect } from "react";
import type {
  Message,
  ToolCall,
  Conversation,
  ContentEventData,
  ToolCallEventData,
  ToolResultEventData,
  DoneEventData,
} from "@/types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const API_TOKEN = (import.meta.env.VITE_MANUS_API_TOKEN || "").trim();
const ACTIVE_CONVERSATION_STORAGE_KEY = "manus.active_conversation_id";
const AUTOLOAD_LATEST_ONCE_KEY = "manus.autoload_latest_once";
const PENDING_EXECUTION_STORAGE_KEY = "manus.pending_execution";
const PENDING_EXECUTION_TTL_MS = 6 * 60 * 60 * 1000; // 6 hours
const CONTINUE_MESSAGES = new Set(["继续", "继续。", "continue", "continue.", "go on"]);

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => {
    window.setTimeout(resolve, ms);
  });
}

function buildAuthHeaders(base: Record<string, string> = {}): Record<string, string> {
  if (!API_TOKEN) return base;
  return { ...base, Authorization: `Bearer ${API_TOKEN}` };
}

function safeGetLocalStorage(key: string): string | null {
  try {
    return window.localStorage.getItem(key);
  } catch {
    return null;
  }
}

function safeSetLocalStorage(key: string, value: string): void {
  try {
    window.localStorage.setItem(key, value);
  } catch {
    // ignore storage failures
  }
}

function safeRemoveLocalStorage(key: string): void {
  try {
    window.localStorage.removeItem(key);
  } catch {
    // ignore storage failures
  }
}

interface PendingExecutionMarker {
  conversationId: string;
  ts: number;
}

function readPendingExecutionMarker(): PendingExecutionMarker | null {
  const raw = safeGetLocalStorage(PENDING_EXECUTION_STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw) as PendingExecutionMarker;
    if (
      !parsed
      || typeof parsed.conversationId !== "string"
      || !parsed.conversationId
      || typeof parsed.ts !== "number"
    ) {
      safeRemoveLocalStorage(PENDING_EXECUTION_STORAGE_KEY);
      return null;
    }
    if (Date.now() - parsed.ts > PENDING_EXECUTION_TTL_MS) {
      safeRemoveLocalStorage(PENDING_EXECUTION_STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    safeRemoveLocalStorage(PENDING_EXECUTION_STORAGE_KEY);
    return null;
  }
}

function writePendingExecutionMarker(conversationId: string): void {
  safeSetLocalStorage(
    PENDING_EXECUTION_STORAGE_KEY,
    JSON.stringify({ conversationId, ts: Date.now() }),
  );
}

function clearPendingExecutionMarker(): void {
  safeRemoveLocalStorage(PENDING_EXECUTION_STORAGE_KEY);
}

interface AgentState {
  conversations: Conversation[];
  messages: Message[];
  isLoading: boolean;
  isThinking: boolean;
  currentToolCall: ToolCall | null;
  conversationId: string | null;
  error: string | null;
  iteration: number;
  limitReached: boolean;
  continueMessage: string | null;
}

interface SendMessageOptions {
  silentUserMessage?: boolean;
  conversationIdOverride?: string | null;
  controlContinue?: boolean;
}

interface ConversationListResponse {
  conversations?: Array<{
    id: string;
    title: string;
    message_count: number;
    created_at: string;
    awaiting_resume?: boolean;
    resume_pending?: boolean;
  }>;
}

interface ConversationDetailResponse {
  id: string;
  title: string;
  messages?: Array<{
    id: string;
    role: "user" | "assistant" | "tool";
    content: string;
    timestamp: string;
    tool_calls?: Array<{
      id: string;
      name: string;
      arguments?: Record<string, unknown>;
      result?: unknown;
      status?: ToolCall["status"];
    }>;
  }>;
  limit_reached?: boolean;
  continue_message?: string | null;
  awaiting_resume?: boolean;
  resume_pending?: boolean;
  created_at: string;
}

type ConversationDetailMessage = NonNullable<ConversationDetailResponse["messages"]>[number];

function normalizeConversations(payload: ConversationListResponse): Conversation[] {
  const items = payload.conversations || [];
  return items.map((item) => ({
    id: item.id,
    title: item.title || "新对话",
    messages: [],
    messageCount: item.message_count || 0,
    createdAt: item.created_at || new Date().toISOString(),
    awaitingResume: Boolean(item.awaiting_resume),
    resumePending: Boolean(item.resume_pending),
  }));
}

function normalizeMessage(item: ConversationDetailMessage): Message {
  return {
    id: item?.id || crypto.randomUUID(),
    role: item?.role || "assistant",
    content: item?.content || "",
    toolCalls: (item?.tool_calls || []).map((tc) => ({
      id: tc.id || crypto.randomUUID(),
      name: tc.name || "",
      arguments: tc.arguments || {},
      result: typeof tc.result === "string"
        ? tc.result
        : tc.result === undefined || tc.result === null
          ? undefined
          : JSON.stringify(tc.result),
      status: tc.status || "completed",
    })),
    timestamp: item?.timestamp || new Date().toISOString(),
  };
}

function inferContinueStateFromMessages(messages: Message[]): {
  limitReached: boolean;
  continueMessage: string | null;
} {
  const hint = "已达到单次最大调用轮数";
  for (let i = messages.length - 1; i >= 0; i -= 1) {
    const msg = messages[i];
    if (msg.role !== "assistant" || !msg.content) continue;
    if (msg.content.includes(hint)) {
      return {
        limitReached: true,
        continueMessage: msg.content,
      };
    }
  }
  return {
    limitReached: false,
    continueMessage: null,
  };
}

function isContinueCommand(message: string): boolean {
  const text = (message || "").trim().toLowerCase();
  return CONTINUE_MESSAGES.has(text);
}

export function useAgent() {
  const [state, setState] = useState<AgentState>({
    conversations: [],
    messages: [],
    isLoading: false,
    isThinking: false,
    currentToolCall: null,
    conversationId: null,
    error: null,
    iteration: 0,
    limitReached: false,
    continueMessage: null,
  });

  const abortControllerRef = useRef<AbortController | null>(null);
  const bootstrappedRef = useRef(false);
  const interruptedConversationRef = useRef<Record<string, boolean>>({});
  const awaitingResumeConversationRef = useRef<Record<string, boolean>>({});
  const resumePendingConversationRef = useRef<Record<string, boolean>>({});
  const messageCountConversationRef = useRef<Record<string, number>>({});

  const fetchConversations = useCallback(async (): Promise<Conversation[]> => {
    try {
      const res = await fetch(`${API_BASE}/api/conversations`, {
        headers: buildAuthHeaders(),
      });
      if (!res.ok) return [];

      const data = (await res.json()) as ConversationListResponse;
      const conversations = normalizeConversations(data);

      setState((prev) => ({
        ...prev,
        conversations,
      }));
      return conversations;
    } catch {
      // ignore list fetch errors
      return [];
    }
  }, []);

  const loadConversation = useCallback(async (conversationId: string) => {
    try {
      const res = await fetch(`${API_BASE}/api/conversations/${conversationId}`, {
        headers: buildAuthHeaders(),
      });
      if (!res.ok) {
        throw new Error(`HTTP ${res.status}: ${res.statusText}`);
      }

      const data = (await res.json()) as ConversationDetailResponse;
      const messages = (data.messages || [])
        .filter((msg) => msg.role !== "tool")
        .map(normalizeMessage);
      const interrupted = (
        messages.length > 0
        && messages[messages.length - 1]?.role === "user"
      );
      interruptedConversationRef.current[data.id] = interrupted;
      awaitingResumeConversationRef.current[data.id] = Boolean(data.awaiting_resume);
      resumePendingConversationRef.current[data.id] = Boolean(data.resume_pending);
      messageCountConversationRef.current[data.id] = messages.length;
      const inferred = inferContinueStateFromMessages(messages);
      const backendLimitReached = Boolean(data.limit_reached);
      const backendContinueMessage =
        typeof data.continue_message === "string" && data.continue_message.trim()
          ? data.continue_message
          : null;
      const limitReached = backendLimitReached || inferred.limitReached;
      const continueMessage = limitReached
        ? (
            backendContinueMessage
            || inferred.continueMessage
            || "已达到单次最大调用轮数。点击“继续”可接着执行。"
          )
        : null;

      setState((prev) => ({
        ...prev,
        messages,
        conversationId: data.id,
        isLoading: false,
        isThinking: false,
        currentToolCall: null,
        error: null,
        iteration: 0,
        limitReached,
        continueMessage,
      }));
      safeSetLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY, data.id);

      return true;
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "加载历史对话失败",
      }));
      return false;
    }
  }, []);

  const sendMessage = useCallback(async (message: string, options?: SendMessageOptions) => {
    const controlContinue = Boolean(
      options?.controlContinue !== undefined
        ? options.controlContinue
        : isContinueCommand(message)
    );
    const silentUserMessage = Boolean(options?.silentUserMessage || controlContinue);
    const effectiveConversationId = options?.conversationIdOverride ?? state.conversationId;

    if (!silentUserMessage) {
      // 添加用户消息
      const userMessage: Message = {
        id: crypto.randomUUID(),
        role: "user",
        content: message,
        timestamp: new Date().toISOString(),
      };

      setState((prev) => ({
        ...prev,
        messages: [...prev.messages, userMessage],
        isLoading: true,
        isThinking: true,
        error: null,
        iteration: 0,
        limitReached: false,
        continueMessage: null,
      }));
    } else {
      setState((prev) => ({
        ...prev,
        isLoading: true,
        isThinking: true,
        error: null,
        iteration: 0,
        limitReached: false,
        continueMessage: null,
      }));
    }

    // 准备 assistant 消息占位
    const assistantMsgId = crypto.randomUUID();
    let assistantContent = "";
    const toolCalls: ToolCall[] = [];

    try {
      abortControllerRef.current = new AbortController();
      const hasConversationId = Boolean(effectiveConversationId);
      if (!hasConversationId) {
        // 新会话尚未收到 conversation_id 时刷新，下一次启动自动恢复到最新会话
        safeSetLocalStorage(AUTOLOAD_LATEST_ONCE_KEY, "1");
        writePendingExecutionMarker("_latest");
      } else {
        writePendingExecutionMarker(effectiveConversationId || "_latest");
      }

      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: buildAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          message,
          conversation_id: effectiveConversationId,
          control_continue: controlContinue,
        }),
        signal: abortControllerRef.current.signal,
      });

      if (!response.ok) {
        throw new Error(`HTTP ${response.status}: ${response.statusText}`);
      }

      const reader = response.body?.getReader();
      if (!reader) throw new Error("No response body");

      const decoder = new TextDecoder();
      let buffer = "";

      while (true) {
        const { done, value } = await reader.read();
        if (done) break;

        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split("\n");
        buffer = lines.pop() || "";

        let currentEvent = "";

        for (const line of lines) {
          if (line.startsWith("event:")) {
            currentEvent = line.slice(6).trim();
          } else if (line.startsWith("data:")) {
            const dataStr = line.slice(5).trim();
            if (!dataStr) continue;

            try {
              const data = JSON.parse(dataStr);

              switch (currentEvent) {
                case "thinking":
                  setState((prev) => ({
                    ...prev,
                    isThinking: true,
                    iteration: data.iteration || prev.iteration,
                  }));
                  break;

                case "content": {
                  const contentData = data as ContentEventData;
                  if (contentData.type === "conversation_info") {
                    if (contentData.conversation_id) {
                      safeSetLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY, contentData.conversation_id);
                      safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
                      writePendingExecutionMarker(contentData.conversation_id);
                    }
                    setState((prev) => ({
                      ...prev,
                      conversationId: contentData.conversation_id || prev.conversationId,
                    }));
                  } else {
                    assistantContent = contentData.type === "final_answer"
                      ? contentData.content
                      : assistantContent + (contentData.content || "");

                    setState((prev) => {
                      const msgs = [...prev.messages];
                      const existingIdx = msgs.findIndex((m) => m.id === assistantMsgId);
                      const assistantMsg: Message = {
                        id: assistantMsgId,
                        role: "assistant",
                        content: assistantContent,
                        toolCalls: [...toolCalls],
                        timestamp: new Date().toISOString(),
                      };
                      if (existingIdx >= 0) {
                        msgs[existingIdx] = assistantMsg;
                      } else {
                        msgs.push(assistantMsg);
                      }
                      return { ...prev, messages: msgs, isThinking: false };
                    });
                  }
                  break;
                }

                case "tool_call": {
                  const tcData = data as ToolCallEventData;
                  const tc: ToolCall = {
                    id: tcData.id,
                    name: tcData.name,
                    arguments: tcData.arguments,
                    status: "running",
                  };
                  toolCalls.push(tc);

                  setState((prev) => {
                    const msgs = [...prev.messages];
                    const existingIdx = msgs.findIndex((m) => m.id === assistantMsgId);
                    const assistantMsg: Message = {
                      id: assistantMsgId,
                      role: "assistant",
                      content: assistantContent,
                      toolCalls: [...toolCalls],
                      timestamp: new Date().toISOString(),
                    };
                    if (existingIdx >= 0) {
                      msgs[existingIdx] = assistantMsg;
                    } else {
                      msgs.push(assistantMsg);
                    }
                    return {
                      ...prev,
                      messages: msgs,
                      isThinking: false,
                      currentToolCall: tc,
                    };
                  });
                  break;
                }

                case "tool_result": {
                  const trData = data as ToolResultEventData;
                  const tcIdx = toolCalls.findIndex((tc) => tc.id === trData.id);
                  if (tcIdx >= 0) {
                    toolCalls[tcIdx] = {
                      ...toolCalls[tcIdx],
                      result: trData.result,
                      status: trData.status as ToolCall["status"],
                    };
                  }

                  setState((prev) => {
                    const msgs = [...prev.messages];
                    const existingIdx = msgs.findIndex((m) => m.id === assistantMsgId);
                    const assistantMsg: Message = {
                      id: assistantMsgId,
                      role: "assistant",
                      content: assistantContent,
                      toolCalls: [...toolCalls],
                      timestamp: new Date().toISOString(),
                    };
                    if (existingIdx >= 0) {
                      msgs[existingIdx] = assistantMsg;
                    } else {
                      msgs.push(assistantMsg);
                    }
                    return {
                      ...prev,
                      messages: msgs,
                      currentToolCall: null,
                    };
                  });
                  break;
                }

                case "done":
                  {
                    const doneData = data as DoneEventData;
                    const limitReached = Boolean(doneData.limit_reached);
                    const alreadyRunning = Boolean(doneData.already_running);
                    const defaultContinueMessage = doneData.max_iterations
                      ? `已达到单次最大调用轮数（${doneData.max_iterations} 轮）。点击“继续”可接着执行。`
                      : "已达到单次最大调用轮数。点击“继续”可接着执行。";
                    setState((prev) => ({
                      ...prev,
                      isLoading: false,
                      isThinking: false,
                      currentToolCall: null,
                      limitReached,
                      continueMessage: limitReached
                        ? (doneData.continue_message || defaultContinueMessage)
                        : null,
                    }));
                    if (!alreadyRunning) {
                      clearPendingExecutionMarker();
                    }
                    void fetchConversations();
                  }
                  break;

                case "error":
                  clearPendingExecutionMarker();
                  setState((prev) => ({
                    ...prev,
                    isLoading: false,
                    isThinking: false,
                    error: typeof data === "string" ? data : data.message || "未知错误",
                  }));
                  break;
              }
            } catch {
              // JSON parse error, skip
            }
          }
        }
      }
    } catch (err) {
      if (err instanceof Error && err.name === "AbortError") {
        return;
      }
      clearPendingExecutionMarker();
      setState((prev) => ({
        ...prev,
        isLoading: false,
        isThinking: false,
        error: err instanceof Error ? err.message : "连接失败",
      }));
    }
  }, [state.conversationId, fetchConversations]);

  const stopAgent = useCallback(() => {
    clearPendingExecutionMarker();
    safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
    abortControllerRef.current?.abort();
    setState((prev) => ({
      ...prev,
      isLoading: false,
      isThinking: false,
      currentToolCall: null,
    }));
  }, []);

  const continueAgent = useCallback(() => {
    if (state.isLoading || !state.conversationId) return;
    void sendMessage("继续", {
      silentUserMessage: true,
      conversationIdOverride: state.conversationId,
      controlContinue: true,
    });
  }, [sendMessage, state.isLoading, state.conversationId]);

  const clearMessages = useCallback(() => {
    safeRemoveLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY);
    safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
    clearPendingExecutionMarker();
    setState((prev) => ({
      ...prev,
      messages: [],
      isLoading: false,
      isThinking: false,
      currentToolCall: null,
      conversationId: null,
      error: null,
      iteration: 0,
      limitReached: false,
      continueMessage: null,
    }));
  }, []);

  useEffect(() => {
    if (bootstrappedRef.current) return;
    bootstrappedRef.current = true;

    let cancelled = false;
    const bootstrap = async () => {
      const savedConversationId = safeGetLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY);
      const shouldAutoloadLatest = safeGetLocalStorage(AUTOLOAD_LATEST_ONCE_KEY) === "1";
      const pendingMarker = readPendingExecutionMarker();
      let conversations = await fetchConversations();

      // 新对话刚发送后立即刷新时，列表可能短暂为空，做一次短轮询恢复
      if (
        !cancelled
        && conversations.length === 0
        && (Boolean(pendingMarker) || shouldAutoloadLatest || Boolean(savedConversationId))
      ) {
        for (let attempt = 0; attempt < 8; attempt += 1) {
          if (cancelled || conversations.length > 0) break;
          await sleep(250);
          conversations = await fetchConversations();
        }
      }

      if (cancelled || conversations.length === 0) return;

      let targetId: string | null = null;
      // 恢复中断任务时，pending 标记优先级最高
      if (pendingMarker) {
        if (pendingMarker.conversationId === "_latest") {
          targetId = conversations[0].id;
        } else if (conversations.some((item) => item.id === pendingMarker.conversationId)) {
          targetId = pendingMarker.conversationId;
        }
      }

      if (!targetId) {
        if (
          savedConversationId
          && conversations.some((item) => item.id === savedConversationId)
        ) {
          targetId = savedConversationId;
        } else if (shouldAutoloadLatest) {
          targetId = conversations[0].id;
        }
      }

      if (!targetId) {
        // 兜底：刷新后默认打开最近会话，避免中间区空白
        targetId = conversations[0].id;
      }

      safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
      if (targetId) {
        const ok = await loadConversation(targetId);
        if (!ok || cancelled) return;

        const pendingMatched = Boolean(
          pendingMarker
          && (pendingMarker.conversationId === targetId || pendingMarker.conversationId === "_latest")
        );
        const interruptedByHistory = Boolean(interruptedConversationRef.current[targetId]);
        const awaitingResumeByBackend = Boolean(awaitingResumeConversationRef.current[targetId]);
        const resumePendingByBackend = Boolean(resumePendingConversationRef.current[targetId]);
        const messageCount = messageCountConversationRef.current[targetId] ?? 0;
        const shouldAutoResume = (
          !resumePendingByBackend
          && (
            awaitingResumeByBackend
            || interruptedByHistory
            || (pendingMatched && messageCount === 0)
          )
        );

        if (shouldAutoResume) {
          await sendMessage("继续", {
            silentUserMessage: true,
            conversationIdOverride: targetId,
            controlContinue: true,
          });
        } else if (pendingMatched && !resumePendingByBackend && !awaitingResumeByBackend) {
          clearPendingExecutionMarker();
        }
      }
    };

    void bootstrap();
    return () => {
      cancelled = true;
    };
  }, [fetchConversations, loadConversation, sendMessage]);

  return {
    ...state,
    sendMessage,
    continueAgent,
    fetchConversations,
    loadConversation,
    stopAgent,
    clearMessages,
  };
}
