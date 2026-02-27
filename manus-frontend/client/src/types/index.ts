// ============ Agent 相关类型 ============

export interface ToolCall {
  id: string;
  name: string;
  arguments: Record<string, unknown>;
  result?: string;
  status: "pending" | "running" | "completed" | "failed";
}

export interface ChatImagePayload {
  name: string;
  mime_type: string;
  data_url?: string;
  size_bytes?: number;
  path?: string;
}

export interface Message {
  id: string;
  role: "user" | "assistant" | "tool";
  content: string;
  images?: ChatImagePayload[];
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
  message?: string;
  tool_name?: string;
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

// ============ 计划相关类型 ============

export type PlanPhaseStatus = "pending" | "running" | "completed" | "failed" | string;

export interface PlanPhaseData {
  id: number;
  title: string;
  status: PlanPhaseStatus;
  capabilities?: Record<string, boolean>;
}

export interface TaskPlanData {
  goal: string;
  current_phase_id?: number | null;
  phases: PlanPhaseData[];
}

export interface PlanUpdateEventData {
  reason?: string;
  plan?: TaskPlanData;
  todo_path?: string;
}

// ============ 子代理索引类型 ============

export interface SubAgentSessionRefData {
  session_id: string;
  session_path: string;
  agent_id: string;
  item: string;
  status: string;
  observation_path: string;
}

export interface SubAgentIndexData {
  run_id: string;
  parent_conversation_id: string;
  created_at: string;
  task_template?: string;
  reduce_goal?: string;
  limits?: {
    max_concurrency?: number;
    max_items?: number;
    max_iterations?: number;
  };
  sub_sessions: SubAgentSessionRefData[];
  reduce_summary_path?: string;
  reduce_results_path?: string;
}

export interface SubAgentToolStepData {
  step?: number;
  tool?: string;
  arguments?: Record<string, unknown>;
  result_preview?: string;
  status?: string;
  error?: string;
}

export interface SubAgentMessageToolCallData {
  id: string;
  type: string;
  function: {
    name: string;
    arguments: string;
  };
}

export interface SubAgentMessageData {
  role: string;
  content?: string;
  tool_call_id?: string;
  tool_calls?: SubAgentMessageToolCallData[];
}

export interface SubAgentSessionDetailData {
  id: string;
  run_id?: string;
  parent_conversation_id: string;
  agent_id: string;
  item: string;
  prompt: string;
  workspace?: string;
  status: string;
  iterations: number;
  final_answer: string;
  tool_steps: SubAgentToolStepData[];
  messages: SubAgentMessageData[];
  created_at: string;
  error?: string;
}

export interface DeepResearchSettingsData {
  enabledByDefault: boolean;
  maxConcurrency: number;
  maxItems: number;
  maxIterations: number;
}

// ============ 工具名称映射 ============

export const TOOL_NAMES: Record<string, string> = {
  web_search: "网页搜索",
  wide_research: "并行研究",
  spawn_sub_agents: "子代理并行",
  shell_exec: "终端命令",
  execute_code: "代码执行",
  browser_navigate: "浏览网页",
  browser_screenshot: "页面截图",
  browser_get_content: "获取页面内容",
  browser_click: "点击页面元素",
  browser_input: "输入文本",
  browser_scroll: "滚动页面",
  read_file: "读取文件",
  write_file: "写入文件",
  edit_file: "编辑文件",
  append_file: "追加文件",
  list_files: "列出目录",
  find_files: "查找文件",
  grep_files: "搜索内容",
  data_analysis: "数据分析",
};

export const TOOL_ICONS: Record<string, string> = {
  web_search: "Globe",
  wide_research: "ListTodo",
  spawn_sub_agents: "ListTodo",
  shell_exec: "Terminal",
  execute_code: "Code",
  browser_navigate: "Globe",
  browser_screenshot: "Camera",
  browser_get_content: "FileText",
  browser_click: "MousePointer",
  browser_input: "Keyboard",
  browser_scroll: "ArrowDown",
  read_file: "FileText",
  write_file: "FilePlus",
  edit_file: "FileEdit",
  append_file: "FilePlus2",
  list_files: "FolderTree",
  find_files: "Search",
  grep_files: "FileSearch",
  data_analysis: "BarChart3",
};
