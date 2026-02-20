/**
 * MessageBubble - 对话消息气泡
 * 设计风格: Glass Workspace
 * 用户消息右对齐，Agent 消息左对齐
 */
import { motion } from "framer-motion";
import { User, Bot } from "lucide-react";
import { Streamdown } from "streamdown";
import type { Message } from "@/types";
import ToolCallCard from "./ToolCallCard";

interface MessageBubbleProps {
  message: Message;
}

export default function MessageBubble({ message }: MessageBubbleProps) {
  const isUser = message.role === "user";

  return (
    <motion.div
      initial={{ opacity: 0, y: 12 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.35, ease: [0.23, 1, 0.32, 1] }}
      className={`flex gap-3 ${isUser ? "flex-row-reverse" : "flex-row"} mb-4`}
    >
      {/* 头像 */}
      <div
        className={`shrink-0 w-8 h-8 rounded-xl flex items-center justify-center ${
          isUser
            ? "bg-primary/20 border border-primary/30"
            : "bg-emerald-500/20 border border-emerald-500/30"
        }`}
      >
        {isUser ? (
          <User className="w-4 h-4 text-primary" />
        ) : (
          <Bot className="w-4 h-4 text-emerald-400" />
        )}
      </div>

      {/* 消息内容 */}
      <div className={`max-w-[75%] ${isUser ? "items-end" : "items-start"}`}>
        {/* 文本内容 */}
        {message.content && (
          <div
            className={`rounded-2xl px-4 py-3 ${
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
              <div className="text-sm leading-relaxed prose prose-invert prose-sm max-w-none [&_pre]:bg-black/40 [&_pre]:rounded-lg [&_code]:text-primary/90 [&_code]:font-mono [&_a]:text-primary [&_a]:no-underline [&_a:hover]:underline">
                <Streamdown>{message.content}</Streamdown>
              </div>
            )}
          </div>
        )}

        {/* 工具调用 */}
        {message.toolCalls && message.toolCalls.length > 0 && (
          <div className="mt-2 space-y-1">
            {message.toolCalls.map((tc) => (
              <ToolCallCard key={tc.id} toolCall={tc} />
            ))}
          </div>
        )}
      </div>
    </motion.div>
  );
}
