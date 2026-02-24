/**
 * MessageBubble - 对话消息气泡
 * 
 * 对齐 Manus 1.6 Max 风格：
 * - 用户消息右对齐，Agent 消息左对齐
 * - 工具调用以紧凑的单行列表嵌入消息流中
 * - Agent 文本消息使用增强版 Markdown 渲染（含代码高亮）
 */
import { motion } from "framer-motion";
import { User, Bot } from "lucide-react";
import type { Message } from "@/types";
import ToolCallCard from "./ToolCallCard";
import MarkdownRenderer from "./MarkdownRenderer";

interface MessageBubbleProps {
  message: Message;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";
  const hasToolCalls = message.toolCalls && message.toolCalls.length > 0;
  const hasContent = !!message.content;

  return (
    <motion.div
      initial={{ opacity: 0, y: 8 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.25, ease: [0.23, 1, 0.32, 1] }}
      className={`flex gap-2.5 ${isUser ? "flex-row-reverse" : "flex-row"} mb-3`}
    >
      {/* 头像 */}
      <div
        className={`shrink-0 w-7 h-7 rounded-lg flex items-center justify-center mt-0.5 ${
          isUser
            ? "bg-primary/20 border border-primary/30"
            : "bg-emerald-500/20 border border-emerald-500/30"
        }`}
      >
        {isUser ? (
          <User className="w-3.5 h-3.5 text-primary" />
        ) : (
          <Bot className="w-3.5 h-3.5 text-emerald-400" />
        )}
      </div>

      {/* 消息内容 */}
      <div className={`max-w-[80%] min-w-0 ${isUser ? "items-end" : "items-start"}`}>
        {/* 文本内容 */}
        {hasContent && (
          <div
            className={`rounded-2xl px-4 py-2.5 ${
              isUser
                ? "bg-primary/20 border border-primary/20 text-foreground"
                : "glass text-foreground"
            }`}
          >
            {isUser ? (
              <p className="text-sm leading-relaxed whitespace-pre-wrap">
                {message.content}
              </p>
            ) : (
              <MarkdownRenderer content={message.content!} />
            )}
          </div>
        )}

        {/* 工具调用列表 - 紧凑的单行标签 */}
        {hasToolCalls && (
          <div className={`${hasContent ? "mt-1.5" : ""}`}>
            {message.toolCalls!.map((tc) => (
              <ToolCallCard key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}
