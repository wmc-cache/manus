// ============ Agent 相关类型 ============

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  status: "pending" | "running" | "completed" | "failed";
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  toolCalls?: ToolCall[];
  thinking?: string;
  timestamp: string;
}

export interface Conversation {
  id: string;
  title: string;
  messages: Message[];
  messageCount?: number;
  createdAt: string;
  awaitingResume?: boolean;
  resumePending?: boolean;
}

// ============ SSE 事件类型 ============

export type SSEEventType =
  | "thinking"
  | "content"
  | "tool_call"
  | "tool_result"
  | "plan_update"
  | "done"
  | "error";

export interface SSEEvent {
  event: SSEEventType;
  data: string;
}

export interface ThinkingEventData {
  iteration: number;
  status: string;
}

export interface ContentEventData {
  content: string;
  type: "conversation_info" | "intermediate" | "final_answer";
  conversation_id?: string;
}

export interface ToolCallEventData {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  status: string;
}

export interface ToolResultEventData {
  id: string;
  name: string;
  result: string;
  status: string;
}

export interface DoneEventData {
  conversation_id: string;
  iterations: number;
  limit_reached?: boolean;
  max_iterations?: number;
  continue_message?: string;
  already_running?: boolean;
}

// ============ 工具名称映射 ============

export const TOOL_NAMES: Record<string, string> = {
  web_search: "网页搜索",
  wide_research: "并行研究",
  shell_exec: "终端命令",
  execute_code: "代码执行",
  browser_navigate: "浏览网页",
  browser_screenshot: "页面截图",
  browser_get_content: "获取页面内容",
  read_file: "读取文件",
  write_file: "写入文件",
};

export const TOOL_ICONS: Record<string, string> = {
  web_search: "Globe",
  wide_research: "ListTodo",
  shell_exec: "Terminal",
  execute_code: "Code",
  browser_navigate: "Globe",
  browser_screenshot: "Camera",
  browser_get_content: "FileText",
  read_file: "FileText",
  write_file: "FilePlus",
};
