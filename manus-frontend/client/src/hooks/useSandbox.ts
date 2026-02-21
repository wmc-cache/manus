/**
 * useSandbox - 管理计算机窗口 WebSocket 连接和沙箱事件
 * 支持按 conversation_id 隔离：每个对话有独立的终端、编辑器、浏览器、文件系统
 */
import { useState, useEffect, useCallback, useRef } from "react";

const API_BASE = (import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000").replace(/\/$/, "");
const WS_BASE = API_BASE.startsWith("https://")
  ? API_BASE.replace(/^https:/, "wss:")
  : API_BASE.replace(/^http:/, "ws:");
const WS_URL = `${WS_BASE}/ws/sandbox`;

export interface SandboxEvent {
  type: string;
  data: Record<string, unknown>;
  window_id?: string;
  conversation_id?: string;
  timestamp: string;
}

export interface FileNode {
  name: string;
  path: string;
  type: "file" | "directory";
  icon: string;
  language?: string;
  size?: number;
  is_text?: boolean;
  children?: FileNode[];
}

export interface FileContent {
  path: string;
  name: string;
  content: string;
  language: string;
  is_text: boolean;
  size: number;
}

export interface TerminalData {
  sessionId: string;
  output: string;
}

export interface BrowserData {
  url: string;
  title: string;
  screenshot: string; // base64
  status?: number;
}

export type ActiveWindow = "terminal" | "editor" | "browser";

export function useSandbox() {
  const [connected, setConnected] = useState(false);
  const [activeWindow, setActiveWindow] = useState<ActiveWindow>("terminal");
  const [terminalOutput, setTerminalOutput] = useState<string>("");
  const [browserData, setBrowserData] = useState<BrowserData | null>(null);
  const [editorFile, setEditorFile] = useState<FileContent | null>(null);
  const [fileTree, setFileTree] = useState<FileNode[]>([]);
  const [events, setEvents] = useState<SandboxEvent[]>([]);

  const wsRef = useRef<WebSocket | null>(null);
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const fetchFileTreeRef = useRef<() => void>(() => {});
  // 当前订阅的 conversation_id
  const currentConvIdRef = useRef<string | null>(null);

  // 获取文件树（按 conversation_id 隔离）
  const fetchFileTree = useCallback(async () => {
    try {
      const convId = currentConvIdRef.current;
      const params = convId ? `?conversation_id=${encodeURIComponent(convId)}` : "";
      const res = await fetch(`${API_BASE}/api/sandbox/files${params}`);
      const data = await res.json();
      setFileTree(data.tree || []);
    } catch {
      // ignore
    }
  }, []);

  // 保持 ref 始终指向最新的 fetchFileTree
  useEffect(() => {
    fetchFileTreeRef.current = fetchFileTree;
  }, [fetchFileTree]);

  // 切换对话 — 重置所有计算机窗口状态，通知后端切换事件订阅
  const switchConversation = useCallback((conversationId: string | null) => {
    currentConvIdRef.current = conversationId;

    // 重置所有窗口状态
    setTerminalOutput("");
    setBrowserData(null);
    setEditorFile(null);
    setFileTree([]);
    setEvents([]);
    setActiveWindow("terminal");

    // 通知后端 WebSocket 切换订阅
    if (wsRef.current?.readyState === WebSocket.OPEN && conversationId) {
      wsRef.current.send(
        JSON.stringify({
          type: "subscribe_conversation",
          conversation_id: conversationId,
        })
      );
    }

    // 刷新新对话的文件树
    fetchFileTreeRef.current();
  }, []);

  // WebSocket 连接
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return;

    try {
      const ws = new WebSocket(WS_URL);
      wsRef.current = ws;

      ws.onopen = () => {
        setConnected(true);
        // 如果已有 conversation_id，重新订阅
        const convId = currentConvIdRef.current;
        if (convId) {
          ws.send(
            JSON.stringify({
              type: "subscribe_conversation",
              conversation_id: convId,
            })
          );
        }
        fetchFileTreeRef.current();
      };

      ws.onmessage = (event) => {
        try {
          const data: SandboxEvent = JSON.parse(event.data);

          // 只处理当前对话的事件（或无 conversation_id 的全局事件）
          const eventConvId = data.conversation_id;
          const currentConvId = currentConvIdRef.current;
          if (eventConvId && currentConvId && eventConvId !== currentConvId) {
            return; // 忽略其他对话的事件
          }

          setEvents((prev) => [...prev.slice(-200), data]);

          // 根据事件类型更新对应窗口
          switch (data.type) {
            case "terminal_output":
              setTerminalOutput((prev) => prev + (data.data.data as string));
              setActiveWindow("terminal");
              break;

            case "terminal_command":
              setTerminalOutput((prev) => prev + `\n$ ${data.data.command}\n`);
              setActiveWindow("terminal");
              break;

            case "terminal_started":
              setTerminalOutput("");
              break;

            case "browser_navigating":
              setBrowserData((prev) => ({
                url: data.data.url as string,
                title: "加载中...",
                screenshot: prev?.screenshot || "",
              }));
              setActiveWindow("browser");
              break;

            case "browser_navigated":
            case "browser_screenshot":
            case "browser_clicked":
              setBrowserData({
                url: (data.data.url as string) || "",
                title: (data.data.title as string) || "",
                screenshot: (data.data.screenshot as string) || "",
                status: data.data.status as number,
              });
              setActiveWindow("browser");
              break;

            case "file_opened":
              setEditorFile({
                path: (data.data.path as string) || "",
                name: (data.data.name as string) || "",
                content: (data.data.content as string) || "",
                language: (data.data.language as string) || "plaintext",
                is_text: true,
                size: ((data.data.content as string) || "").length,
              });
              setActiveWindow("editor");
              fetchFileTreeRef.current();
              break;

            case "file_changed":
              fetchFileTreeRef.current();
              break;
          }
        } catch {
          // ignore parse errors
        }
      };

      ws.onclose = () => {
        setConnected(false);
        reconnectTimerRef.current = setTimeout(connect, 3000);
      };

      ws.onerror = () => {
        ws.close();
      };
    } catch {
      reconnectTimerRef.current = setTimeout(connect, 3000);
    }
  }, []);

  // 发送终端输入
  const sendTerminalInput = useCallback((data: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(
        JSON.stringify({
          type: "terminal_input",
          session_id: "default",
          data,
        })
      );
    }
  }, []);

  // 获取文件内容（点击文件时调用，带 conversation_id）
  const fetchFileContent = useCallback(async (path: string) => {
    try {
      const convId = currentConvIdRef.current;
      const params = new URLSearchParams({ path });
      if (convId) params.set("conversation_id", convId);
      const res = await fetch(`${API_BASE}/api/sandbox/files/content?${params.toString()}`);
      const data = await res.json();
      if (!data.error) {
        setEditorFile(data as FileContent);
        setActiveWindow("editor");
      }
    } catch {
      // ignore
    }
  }, []);

  // 初始化
  useEffect(() => {
    connect();
    fetchFileTree();

    return () => {
      clearTimeout(reconnectTimerRef.current);
      wsRef.current?.close();
    };
  }, [connect, fetchFileTree]);

  return {
    connected,
    activeWindow,
    setActiveWindow,
    terminalOutput,
    setTerminalOutput,
    browserData,
    editorFile,
    fileTree,
    events,
    sendTerminalInput,
    fetchFileTree,
    fetchFileContent,
    switchConversation,
  };
}
