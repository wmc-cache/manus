import { useState, useCallback, useRef } from "react";
import type {
  Message,
  ToolCall,
  ContentEventData,
  ToolCallEventData,
  ToolResultEventData,
  DoneEventData,
} from "@/types";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");

interface AgentState {
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

export function useAgent() {
  const [state, setState] = useState<AgentState>({
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
        headers: { "Content-Type": "application/json" },
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
  }, [state.conversationId]);

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
    setState({
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
  }, []);

  return {
    ...state,
    sendMessage,
    continueAgent,
    stopAgent,
    clearMessages,
  };
}
