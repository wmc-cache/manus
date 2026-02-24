/**
 * EmptyState - 空对话状态
 * 
 * [P2优化] 本地化静态资源：
 * - 将外部 CDN 图片替换为 SVG 内联图标，避免加载失败
 * - 增加更多样化的建议卡片
 */
import { motion } from "framer-motion";
import { Globe, Code, FileText, Sparkles, Zap, BarChart3 } from "lucide-react";

const SUGGESTIONS = [
  {
    icon: Globe,
    title: "搜索信息",
    description: "帮我搜索最新的 AI 行业动态",
    color: "text-blue-400",
    bg: "bg-blue-500/10",
    border: "border-blue-500/20",
  },
  {
    icon: Code,
    title: "编写代码",
    description: "写一个 Python 爬虫脚本",
    color: "text-emerald-400",
    bg: "bg-emerald-500/10",
    border: "border-emerald-500/20",
  },
  {
    icon: FileText,
    title: "处理文件",
    description: "帮我整理数据并生成报告",
    color: "text-amber-400",
    bg: "bg-amber-500/10",
    border: "border-amber-500/20",
  },
  {
    icon: Sparkles,
    title: "创意任务",
    description: "帮我写一篇技术博客文章",
    color: "text-purple-400",
    bg: "bg-purple-500/10",
    border: "border-purple-500/20",
  },
  {
    icon: BarChart3,
    title: "数据分析",
    description: "分析这份 CSV 数据并可视化",
    color: "text-rose-400",
    bg: "bg-rose-500/10",
    border: "border-rose-500/20",
  },
  {
    icon: Zap,
    title: "自动化",
    description: "帮我批量处理这些文件",
    color: "text-cyan-400",
    bg: "bg-cyan-500/10",
    border: "border-cyan-500/20",
  },
];

interface EmptyStateProps {
  onSuggestionClick: (text: string) => void;
}

/** 内联 SVG Logo - 不依赖外部 CDN */
function ManusLogo() {
  return (
    <motion.div
      className="w-24 h-24 mx-auto mb-6 rounded-2xl bg-gradient-to-br from-primary/20 via-emerald-500/15 to-blue-500/20 border border-white/10 flex items-center justify-center shadow-lg shadow-primary/10"
      animate={{
        y: [0, -6, 0],
      }}
      transition={{
        duration: 4,
        repeat: Infinity,
        ease: "easeInOut",
      }}
    >
      <svg
        width="48"
        height="48"
        viewBox="0 0 48 48"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
      >
        {/* Lightning bolt / Agent symbol */}
        <path
          d="M26 6L12 28H22L20 42L36 18H25L26 6Z"
          fill="url(#gradient)"
          stroke="rgba(255,255,255,0.2)"
          strokeWidth="1"
          strokeLinejoin="round"
        />
        {/* Orbit rings */}
        <circle cx="24" cy="24" r="20" stroke="rgba(255,255,255,0.08)" strokeWidth="1" fill="none" />
        <circle cx="24" cy="24" r="16" stroke="rgba(255,255,255,0.05)" strokeWidth="0.5" fill="none" />
        <defs>
          <linearGradient id="gradient" x1="12" y1="6" x2="36" y2="42" gradientUnits="userSpaceOnUse">
            <stop stopColor="#22d3ee" />
            <stop offset="0.5" stopColor="#818cf8" />
            <stop offset="1" stopColor="#a78bfa" />
          </linearGradient>
        </defs>
      </svg>
    </motion.div>
  );
}

export default function EmptyState({ onSuggestionClick }: EmptyStateProps) {
  return (
    <div className="flex-1 flex flex-col items-center justify-center px-4">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        animate={{ opacity: 1, scale: 1 }}
        transition={{ duration: 0.5, ease: [0.23, 1, 0.32, 1] }}
        className="text-center max-w-lg"
      >
        <ManusLogo />

        <h2 className="text-xl font-semibold text-foreground mb-2">
          你好，我是 Manus
        </h2>
        <p className="text-sm text-muted-foreground mb-8 leading-relaxed">
          我是一个 AI Agent，可以搜索信息、执行代码、读写文件。
          <br />
          告诉我你想完成什么任务，我会自动规划并执行。
        </p>
      </motion.div>

      {/* 建议卡片 - 3列布局 */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2, duration: 0.4 }}
        className="grid grid-cols-3 gap-3 max-w-2xl w-full"
      >
        {SUGGESTIONS.map((s, i) => (
          <motion.button
            key={i}
            onClick={() => onSuggestionClick(s.description)}
            className={`glass-subtle rounded-xl p-4 text-left hover:bg-white/5 transition-all duration-200 group border ${s.border}`}
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.98 }}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 + i * 0.08 }}
          >
            <div className={`p-2 rounded-lg ${s.bg} w-fit mb-2`}>
              <s.icon className={`w-4 h-4 ${s.color}`} />
            </div>
            <p className="text-sm font-medium text-foreground mb-0.5">
              {s.title}
            </p>
            <p className="text-xs text-muted-foreground line-clamp-2">
              {s.description}
            </p>
          </motion.button>
        ))}
      </motion.div>
    </div>
  );
}
