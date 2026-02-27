/**
 * ChatInput - 消息输入组件
 * 设计风格: 毛玻璃底部栏，自动增长的 textarea
 */
import { useState, useRef, useCallback, useEffect } from "react";
import { motion } from "framer-motion";
import { Send, Square, Sparkles, ImagePlus, X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { Switch } from "@/components/ui/switch";
import type { ChatImagePayload } from "@/types";

const MAX_IMAGE_COUNT = 4;
const MAX_IMAGE_SIZE_BYTES = 6 * 1024 * 1024;

interface PendingImage extends ChatImagePayload {
  id: string;
}

function readFileAsDataUrl(file: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => {
      if (typeof reader.result === "string") {
        resolve(reader.result);
        return;
      }
      reject(new Error("无法读取图片"));
    };
    reader.onerror = () => {
      reject(new Error("图片读取失败"));
    };
    reader.readAsDataURL(file);
  });
}

interface ChatInputProps {
  onSend: (
    message: string,
    options?: { deepResearch?: boolean; images?: ChatImagePayload[] },
  ) => void;
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
  const [images, setImages] = useState<PendingImage[]>([]);
  const [imageError, setImageError] = useState<string | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    setDeepResearch(defaultDeepResearchEnabled);
  }, [defaultDeepResearchEnabled]);

  const handleSubmit = useCallback(() => {
    const trimmed = input.trim();
    const outgoingImages = images.map(({ id, ...image }) => image);
    if ((!trimmed && outgoingImages.length === 0) || isLoading) return;
    onSend(trimmed || "请分析我上传的图片。", { deepResearch, images: outgoingImages });
    setInput("");
    setImages([]);
    setImageError(null);
    if (textareaRef.current) {
      textareaRef.current.style.height = "auto";
    }
    if (fileInputRef.current) {
      fileInputRef.current.value = "";
    }
  }, [deepResearch, images, input, isLoading, onSend]);

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

  const handleImageSelect = useCallback(async (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files || []);
    if (files.length === 0) return;

    const remain = MAX_IMAGE_COUNT - images.length;
    if (remain <= 0) {
      setImageError(`最多上传 ${MAX_IMAGE_COUNT} 张图片`);
      e.target.value = "";
      return;
    }

    const selected = files.slice(0, remain);
    const next: PendingImage[] = [];
    let invalidType = 0;
    let oversize = 0;
    let failed = 0;

    for (const file of selected) {
      if (!file.type.startsWith("image/")) {
        invalidType += 1;
        continue;
      }
      if (file.size > MAX_IMAGE_SIZE_BYTES) {
        oversize += 1;
        continue;
      }
      try {
        const dataUrl = await readFileAsDataUrl(file);
        next.push({
          id: crypto.randomUUID(),
          name: file.name || "image",
          mime_type: file.type || "application/octet-stream",
          size_bytes: file.size,
          data_url: dataUrl,
        });
      } catch {
        failed += 1;
      }
    }

    if (next.length > 0) {
      setImages((prev) => [...prev, ...next]);
    }

    const issues: string[] = [];
    if (invalidType > 0) issues.push(`${invalidType} 个非图片文件`);
    if (oversize > 0) issues.push(`${oversize} 张图片超过 6MB`);
    if (failed > 0) issues.push(`${failed} 张图片读取失败`);
    setImageError(issues.length > 0 ? issues.join("，") : null);
    e.target.value = "";
  }, [images.length]);

  const handleRemoveImage = useCallback((id: string) => {
    setImages((prev) => prev.filter((image) => image.id !== id));
    setImageError(null);
  }, []);

  const hasInput = Boolean(input.trim()) || images.length > 0;

  return (
    <div className="p-4">
      <motion.div
        className="glass rounded-2xl p-2 flex items-end gap-2 max-w-3xl mx-auto"
        initial={{ y: 20, opacity: 0 }}
        animate={{ y: 0, opacity: 1 }}
        transition={{ delay: 0.2, duration: 0.4 }}
      >
        <div className="flex-1 relative">
          {images.length > 0 && (
            <div className="px-2 pt-2 pb-1 flex flex-wrap gap-2">
              {images.map((image) => (
                <div
                  key={image.id}
                  className="relative w-14 h-14 rounded-lg overflow-hidden border border-border/40 bg-background/50"
                >
                  {image.data_url ? (
                    <img src={image.data_url} alt={image.name} className="w-full h-full object-cover" />
                  ) : (
                    <div className="w-full h-full flex items-center justify-center text-[10px] text-muted-foreground">
                      IMG
                    </div>
                  )}
                  <button
                    type="button"
                    onClick={() => handleRemoveImage(image.id)}
                    className="absolute top-0.5 right-0.5 h-4 w-4 rounded-full bg-black/60 text-white flex items-center justify-center"
                    aria-label="移除图片"
                  >
                    <X className="w-3 h-3" />
                  </button>
                </div>
              ))}
            </div>
          )}
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
            <input
              ref={fileInputRef}
              type="file"
              accept="image/*"
              multiple
              onChange={handleImageSelect}
              className="hidden"
              disabled={disabled}
            />
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
              onClick={() => fileInputRef.current?.click()}
              size="icon"
              variant="ghost"
              disabled={disabled}
              className="h-9 w-9 rounded-xl text-muted-foreground hover:text-foreground"
              title="上传图片"
            >
              <ImagePlus className="w-4 h-4" />
            </Button>

            <Button
              onClick={handleSubmit}
              size="icon"
              disabled={!hasInput || disabled}
              className="h-9 w-9 rounded-xl bg-primary/80 hover:bg-primary text-primary-foreground disabled:opacity-30"
            >
              {hasInput ? (
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

      {imageError && (
        <div className="max-w-3xl mx-auto mt-1 px-2 text-xs text-amber-400">
          {imageError}
        </div>
      )}

      {/* <p className="text-center text-xs text-muted-foreground/40 mt-2">
        Manus MVP · Powered by DeepSeek
      </p> */}
    </div>
  );
}
