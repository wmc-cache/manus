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

function buildAuthHeaders(base: Record<string, string> = {}): Record<string, string> {
  if (!API_TOKEN) return base;
  return { ...base, Authorization: `Bearer ${API_TOKEN}` };
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

interface ConversationListResponse {
  conversations?: Array<{
    id: string;
    title: string;
    message_count: number;
    created_at: string;
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

  const fetchConversations = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/api/conversations`, {
        headers: buildAuthHeaders(),
      });
      if (!res.ok) return;

      const data = (await res.json()) as ConversationListResponse;
      const conversations = normalizeConversations(data);

      setState((prev) => ({
        ...prev,
        conversations,
      }));
    } catch {
      // ignore list fetch errors
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

      return true;
    } catch (err) {
      setState((prev) => ({
        ...prev,
        error: err instanceof Error ? err.message : "加载历史对话失败",
      }));
      return false;
    }
  }, []);

  const sendMessage = useCallback(async (message: string) => {
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

    // 准备 assistant 消息占位
    const assistantMsgId = crypto.randomUUID();
    let assistantContent = "";
    const toolCalls: ToolCall[] = [];

    try {
      abortControllerRef.current = new AbortController();

      const response = await fetch(`${API_BASE}/api/chat`, {
        method: "POST",
        headers: buildAuthHeaders({ "Content-Type": "application/json" }),
        body: JSON.stringify({
          message,
          conversation_id: state.conversationId,
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
                    void fetchConversations();
                  }
                  break;

                case "error":
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
      setState((prev) => ({
        ...prev,
        isLoading: false,
        isThinking: false,
        error: err instanceof Error ? err.message : "连接失败",
      }));
    }
  }, [state.conversationId, fetchConversations]);

  const stopAgent = useCallback(() => {
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
    void sendMessage("继续");
  }, [sendMessage, state.isLoading, state.conversationId]);

  const clearMessages = useCallback(() => {
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
    void fetchConversations();
  }, [fetchConversations]);

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
