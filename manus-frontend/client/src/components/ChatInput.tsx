/**
 * ChatInput - 消息输入组件
 * 设计风格: 毛玻璃底部栏，自动增长的 textarea
 */
import { useState, useRef, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Square, Sparkles } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";

interface ChatInputProps {
  onSend: (message: string, options?: { deepResearch?: boolean }) => void;
  onStop: () => void;
  onContinue?: () => void;
  isLoading: boolean;
  disabled?: boolean;
  showContinue?: boolean;
  continueLabel?: string;
  defaultDeepResearchEnabled?: boolean;
}

export default function ChatInput({
  onSend,
  onStop,
  onContinue,
  isLoading,
  disabled,
  showContinue,
  continueLabel = "继续",
  defaultDeepResearchEnabled = false,
}: ChatInputProps) {
  const [input, setInput] = useState("");
  const [deepResearch, setDeepResearch] = useState(defaultDeepResearchEnabled);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  useEffect(() => {
    setDeepResearch(defaultDeepResearchEnabled);
  }, [defaultDeepResearchEnabled]);

  const handleSubmit = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isLoading) return;
    onSend(trimmed, { deepResearch });
    setInput("");
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
  }, [deepResearch, input, isLoading, onSend]);

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSubmit();
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    setInput(e.target.value);
    // 自动调整高度
    const textarea = e.target;
    textarea.style.height = "auto";
    textarea.style.height = Math.min(textarea.scrollHeight, 200) + "px";
  };

  return (
    <div className="p-4">
      <motion.div
        className="glass rounded-2xl p-2 flex items-end gap-2 max-w-3xl mx-auto"
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.2, duration: 0.4 }}
      >
        <div className="flex-1 relative">
          <textarea
            ref={textareaRef}
            value={input}
            onChange={handleInput}
            onKeyDown={handleKeyDown}
            placeholder="输入你的任务，让 Agent 帮你完成..."
            disabled={disabled}
            rows={1}
            className="w-full bg-transparent text-sm text-foreground placeholder:text-muted-foreground/50 resize-none px-3 py-2.5 focus:outline-none max-h-[200px] leading-relaxed"
          />
        </div>

        {isLoading ? (
          <Button
            onClick={onStop}
            size="icon"
            variant="ghost"
            className="shrink-0 h-9 w-9 rounded-xl bg-destructive/20 hover:bg-destructive/30 text-destructive"
          >
            <Square className="w-4 h-4" />
          </Button>
        ) : (
          <div className="shrink-0 flex items-center gap-2">
            {showContinue && onContinue && (
              <Button
                onClick={onContinue}
                size="sm"
                variant="outline"
                disabled={disabled}
                className="h-9 rounded-xl border-primary/40 text-primary hover:bg-primary/10"
              >
                {continueLabel}
              </Button>
            )}

            <Button
              onClick={handleSubmit}
              size="icon"
              disabled={!input.trim() || disabled}
              className="h-9 w-9 rounded-xl bg-primary/80 hover:bg-primary text-primary-foreground disabled:opacity-30"
            >
              {input.trim() ? (
                <Send className="w-4 h-4" />
              ) : (
                <Sparkles className="w-4 h-4" />
              )}
            </Button>
          </div>
        )}
      </motion.div>

      <div className="max-w-3xl mx-auto mt-2 px-2 flex items-center justify-between">
        <label className="inline-flex items-center gap-2 text-xs text-muted-foreground select-none">
          <Switch
            checked={deepResearch}
            onCheckedChange={setDeepResearch}
            disabled={isLoading || disabled}
          />
          深度研究（自动启用子代理并行）
        </label>
      </div>

      <p className="text-center text-xs text-muted-foreground/40 mt-2">
        Manus MVP · Powered by DeepSeek
      </p>
    </div>
  );
}
