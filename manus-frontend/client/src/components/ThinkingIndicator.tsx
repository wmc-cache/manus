/**
 * ThinkingIndicator - Agent 思考状态指示器
 * 设计风格: 脉冲光环 + 毛玻璃
 */
import { motion } from "framer-motion";
import { Brain } from "lucide-react";

interface ThinkingIndicatorProps {
  iteration: number;
  status?: string | null;
}

const STATUS_LABEL: Record<string, string> = {
  thinking: "正在思考",
  waiting_llm: "正在等待模型响应",
  waiting_tool: "正在执行工具",
  queued: "任务排队中",
  already_running: "已有任务在执行",
};

function normalizeStatusLabel(raw: string | null | undefined): string {
  const text = (raw || "").trim();
  if (!text) return STATUS_LABEL.thinking;
  return STATUS_LABEL[text] || text;
}

export default function ThinkingIndicator({ iteration, status }: ThinkingIndicatorProps) {
  const label = normalizeStatusLabel(status);
  return (
    <motion.div
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      exit={{ opacity: 0, y: -10 }}
      className="flex items-center gap-3 px-4 py-3"
    >
      <div className="relative">
        {/* 脉冲光环 */}
        <motion.div
          className="absolute inset-0 rounded-full bg-primary/30"
          animate={{
            scale: [1, 1.8, 1],
            opacity: [0.5, 0, 0.5],
          }}
          transition={{
            duration: 2,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />
        <div className="relative p-2 rounded-full bg-primary/20 border border-primary/30">
          <Brain className="w-4 h-4 text-primary" />
        </div>
      </div>

      <div className="flex items-center gap-2">
        <span className="text-sm text-muted-foreground">
          {label}
          {iteration > 1 && (
            <span className="text-xs text-muted-foreground/60 ml-1">
              (第 {iteration} 轮)
            </span>
          )}
        </span>
        <motion.div className="flex gap-1">
          {[0, 1, 2].map((i) => (
            <motion.div
              key={i}
              className="w-1.5 h-1.5 rounded-full bg-primary/60"
              animate={{
                opacity: [0.3, 1, 0.3],
                scale: [0.8, 1.2, 0.8],
              }}
              transition={{
                duration: 1.2,
                repeat: Infinity,
                delay: i * 0.2,
              }}
            />
          ))}
        </motion.div>
      </div>
    </motion.div>
  );
}
