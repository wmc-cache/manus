import { useState, useCallback, useRef, useEffect } from "react";
import type {
  Message,
  ToolCall,
  Conversation,
  ThinkingEventData,
  ContentEventData,
  ToolCallEventData,
  ToolResultEventData,
  DoneEventData,
  PlanUpdateEventData,
  TaskPlanData,
  SubAgentIndexData,
  SubAgentSessionDetailData,
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
  thinkingStatus: string | null;
  currentToolCall: ToolCall | null;
  conversationId: string | null;
  error: string | null;
  iteration: number;
  limitReached: boolean;
  continueMessage: string | null;
  plan: TaskPlanData | null;
  planReason: string | null;
  todoPath: string | null;
  subAgentIndex: SubAgentIndexData | null;
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
  plan?: TaskPlanData | null;
  sub_agent_index?: unknown;
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

function normalizePlan(raw: unknown): TaskPlanData | null {
  if (!raw || typeof raw !== "object") return null;
  const plan = raw as Partial<TaskPlanData>;
  if (typeof plan.goal !== "string") return null;

  const phases = Array.isArray(plan.phases)
    ? plan.phases
        .filter((phase): phase is TaskPlanData["phases"][number] => (
          !!phase
          && typeof phase === "object"
          && typeof (phase as TaskPlanData["phases"][number]).id === "number"
          && typeof (phase as TaskPlanData["phases"][number]).title === "string"
          && typeof (phase as TaskPlanData["phases"][number]).status === "string"
        ))
        .map((phase) => ({
          id: phase.id,
          title: phase.title,
          status: phase.status,
        }))
    : [];

  return {
    goal: plan.goal,
    phases,
    current_phase_id: typeof plan.current_phase_id === "number" ? plan.current_phase_id : null,
  };
}

function normalizeSubAgentIndex(raw: unknown): SubAgentIndexData | null {
  if (!raw || typeof raw !== "object") return null;
  const index = raw as Partial<SubAgentIndexData>;
  if (
    typeof index.run_id !== "string"
    || typeof index.parent_conversation_id !== "string"
    || typeof index.created_at !== "string"
  ) {
    return null;
  }

  const subSessions = Array.isArray(index.sub_sessions)
    ? index.sub_sessions
        .filter((item): item is SubAgentIndexData["sub_sessions"][number] => (
          !!item
          && typeof item === "object"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).session_id === "string"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).session_path === "string"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).agent_id === "string"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).item === "string"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).status === "string"
          && typeof (item as SubAgentIndexData["sub_sessions"][number]).observation_path === "string"
        ))
        .map((item) => ({
          session_id: item.session_id,
          session_path: item.session_path,
          agent_id: item.agent_id,
          item: item.item,
          status: item.status,
          observation_path: item.observation_path,
        }))
    : [];

  return {
    run_id: index.run_id,
    parent_conversation_id: index.parent_conversation_id,
    created_at: index.created_at,
    task_template: typeof index.task_template === "string" ? index.task_template : undefined,
    reduce_goal: typeof index.reduce_goal === "string" ? index.reduce_goal : undefined,
    sub_sessions: subSessions,
    reduce_summary_path: typeof index.reduce_summary_path === "string" ? index.reduce_summary_path : undefined,
    reduce_results_path: typeof index.reduce_results_path === "string" ? index.reduce_results_path : undefined,
  };
}

function normalizeSubAgentSessionDetail(raw: unknown): SubAgentSessionDetailData | null {
  if (!raw || typeof raw !== "object") return null;
  const session = raw as Partial<SubAgentSessionDetailData>;

  if (
    typeof session.id !== "string"
    || typeof session.parent_conversation_id !== "string"
    || typeof session.agent_id !== "string"
    || typeof session.item !== "string"
    || typeof session.prompt !== "string"
    || typeof session.status !== "string"
    || typeof session.created_at !== "string"
  ) {
    return null;
  }

  const toolSteps = Array.isArray(session.tool_steps)
    ? session.tool_steps
        .filter((step): step is SubAgentSessionDetailData["tool_steps"][number] => !!step && typeof step === "object")
        .map((step) => {
          const normalized: SubAgentSessionDetailData["tool_steps"][number] = {};
          if (typeof step.step === "number") normalized.step = step.step;
          if (typeof step.tool === "string") normalized.tool = step.tool;
          if (step.arguments && typeof step.arguments === "object") {
            normalized.arguments = step.arguments as Record<string, unknown>;
          }
          if (typeof step.result_preview === "string") normalized.result_preview = step.result_preview;
          if (typeof step.status === "string") normalized.status = step.status;
          if (typeof step.error === "string") normalized.error = step.error;
          return normalized;
        })
    : [];

  const messages = Array.isArray(session.messages)
    ? session.messages
        .filter((msg): msg is SubAgentSessionDetailData["messages"][number] => (
          !!msg
          && typeof msg === "object"
          && typeof (msg as SubAgentSessionDetailData["messages"][number]).role === "string"
        ))
        .map((msg) => ({
          role: msg.role,
          content: typeof msg.content === "string" ? msg.content : undefined,
          tool_call_id: typeof msg.tool_call_id === "string" ? msg.tool_call_id : undefined,
          tool_calls: Array.isArray(msg.tool_calls)
            ? msg.tool_calls
                .filter((tc): tc is NonNullable<typeof msg.tool_calls>[number] => (
                  !!tc
                  && typeof tc.id === "string"
                  && typeof tc.type === "string"
                  && !!tc.function
                  && typeof tc.function === "object"
                  && typeof tc.function.name === "string"
                  && typeof tc.function.arguments === "string"
                ))
                .map((tc) => ({
                  id: tc.id,
                  type: tc.type,
                  function: {
                    name: tc.function.name,
                    arguments: tc.function.arguments,
                  },
                }))
            : undefined,
        }))
    : [];

  return {
    id: session.id,
    run_id: typeof session.run_id === "string" ? session.run_id : undefined,
    parent_conversation_id: session.parent_conversation_id,
    agent_id: session.agent_id,
    item: session.item,
    prompt: session.prompt,
    workspace: typeof session.workspace === "string" ? session.workspace : undefined,
    status: session.status,
    iterations: typeof session.iterations === "number" ? session.iterations : 0,
    final_answer: typeof session.final_answer === "string" ? session.final_answer : "",
    tool_steps: toolSteps,
    messages,
    created_at: session.created_at,
    error: typeof session.error === "string" ? session.error : undefined,
  };
}

export function useAgent() {
  const [state, setState] = useState<AgentState>({
    conversations: [],
    messages: [],
    isLoading: false,
    isThinking: false,
    thinkingStatus: null,
    currentToolCall: null,
    conversationId: null,
    error: null,
    iteration: 0,
    limitReached: false,
    continueMessage: null,
    plan: null,
    planReason: null,
    todoPath: null,
    subAgentIndex: null,
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
        thinkingStatus: null,
        currentToolCall: null,
        error: null,
        iteration: 0,
        limitReached,
        continueMessage,
        plan: normalizePlan(data.plan),
        planReason: null,
        todoPath: null,
        subAgentIndex: normalizeSubAgentIndex(data.sub_agent_index),
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

  const loadSubAgentSession = useCallback(async (
    conversationId: string,
    sessionId: string,
  ): Promise<SubAgentSessionDetailData | null> => {
    const convId = (conversationId || "").trim();
    const sid = (sessionId || "").trim();
    if (!convId || !sid) return null;

    try {
      const res = await fetch(
        `${API_BASE}/api/conversations/${encodeURIComponent(convId)}/sub-agents/${encodeURIComponent(sid)}`,
        { headers: buildAuthHeaders() },
      );
      if (!res.ok) return null;
      const data = (await res.json()) as unknown;
      return normalizeSubAgentSessionDetail(data);
    } catch {
      return null;
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
        thinkingStatus: "thinking",
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
        thinkingStatus: "thinking",
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
                  {
                    const thinkingData = data as ThinkingEventData;
                    const rawStatus = typeof thinkingData.status === "string" ? thinkingData.status : "";
                    const rawMessage = typeof thinkingData.message === "string" ? thinkingData.message.trim() : "";
                    const thinkingStatus = rawMessage || rawStatus || "thinking";
                    setState((prev) => ({
                      ...prev,
                      isThinking: true,
                      thinkingStatus,
                      iteration: thinkingData.iteration || prev.iteration,
                    }));
                  }
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
                      thinkingStatus: null,
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

                case "plan_update": {
                  const planData = data as PlanUpdateEventData;
                  const normalizedPlan = normalizePlan(planData.plan);
                  setState((prev) => ({
                    ...prev,
                    plan: normalizedPlan ?? prev.plan,
                    planReason: typeof planData.reason === "string" ? planData.reason : prev.planReason,
                    todoPath: typeof planData.todo_path === "string" ? planData.todo_path : prev.todoPath,
                  }));
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
                      thinkingStatus: null,
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
                    if (doneData.conversation_id) {
                      void loadConversation(doneData.conversation_id);
                    }
                  }
                  break;

                case "error":
                  clearPendingExecutionMarker();
                  setState((prev) => ({
                    ...prev,
                    isLoading: false,
                    isThinking: false,
                    thinkingStatus: null,
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
  }, [state.conversationId, fetchConversations, loadConversation]);

  const stopAgent = useCallback(() => {
    clearPendingExecutionMarker();
    safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
    abortControllerRef.current?.abort();
    setState((prev) => ({
      ...prev,
      isLoading: false,
      isThinking: false,
      thinkingStatus: null,
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

  const deleteConversation = useCallback(async (conversationId: string): Promise<boolean> => {
    const targetId = (conversationId || "").trim();
    if (!targetId) return false;

    const deletingActive = state.conversationId === targetId;
    if (deletingActive && state.isLoading) {
      setState((prev) => ({
        ...prev,
        error: "当前会话正在执行中，无法删除",
      }));
      return false;
    }

    try {
      const response = await fetch(`${API_BASE}/api/conversations/${targetId}`, {
        method: "DELETE",
        headers: buildAuthHeaders(),
      });

      if (!response.ok) {
        let message = `HTTP ${response.status}: ${response.statusText}`;
        try {
          const errorPayload = (await response.json()) as { detail?: string };
          if (errorPayload?.detail) {
            message = errorPayload.detail;
          }
        } catch {
          // ignore parse error
        }
        throw new Error(message);
      }

      delete interruptedConversationRef.current[targetId];
      delete awaitingResumeConversationRef.current[targetId];
      delete resumePendingConversationRef.current[targetId];
      delete messageCountConversationRef.current[targetId];

      const pendingMarker = readPendingExecutionMarker();
      if (pendingMarker && pendingMarker.conversationId === targetId) {
        clearPendingExecutionMarker();
      }
      if (safeGetLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY) === targetId) {
        safeRemoveLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY);
      }

      const conversations = await fetchConversations();
      if (!deletingActive) {
        setState((prev) => ({
          ...prev,
          error: null,
        }));
        return true;
      }

      safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);

      if (conversations.length > 0) {
        const loaded = await loadConversation(conversations[0].id);
        if (loaded) {
          return true;
        }
      }

      setState((prev) => ({
        ...prev,
        messages: [],
        isLoading: false,
        isThinking: false,
        thinkingStatus: null,
        currentToolCall: null,
        conversationId: null,
        error: null,
        iteration: 0,
        limitReached: false,
        continueMessage: null,
        plan: null,
        planReason: null,
        todoPath: null,
        subAgentIndex: null,
      }));
      return true;
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "删除会话失败",
      }));
      return false;
    }
  }, [fetchConversations, loadConversation, state.conversationId, state.isLoading]);

  const clearMessages = useCallback(() => {
    safeRemoveLocalStorage(ACTIVE_CONVERSATION_STORAGE_KEY);
    safeRemoveLocalStorage(AUTOLOAD_LATEST_ONCE_KEY);
    clearPendingExecutionMarker();
    setState((prev) => ({
      ...prev,
      messages: [],
      isLoading: false,
      isThinking: false,
      thinkingStatus: null,
      currentToolCall: null,
      conversationId: null,
      error: null,
      iteration: 0,
      limitReached: false,
      continueMessage: null,
      plan: null,
      planReason: null,
      todoPath: null,
      subAgentIndex: null,
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
    deleteConversation,
    fetchConversations,
    loadConversation,
    loadSubAgentSession,
    stopAgent,
    clearMessages,
  };
}
