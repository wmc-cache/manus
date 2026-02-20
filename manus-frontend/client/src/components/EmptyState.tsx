/**
 * EmptyState - 空对话状态
 * 设计风格: 居中展示，带有生成的空状态图片
 */
import { motion } from "framer-motion";
import { Globe, Code, FileText, Sparkles } from "lucide-react";

const EMPTY_STATE_IMG =
  "https://private-us-east-1.manuscdn.com/sessionFile/wYRFO7o4twJWKVfWlISpqY/sandbox/drOIOkPpOFG7RUVTquZoNi-img-3_1771589343000_na1fn_ZW1wdHktc3RhdGU.png?x-oss-process=image/resize,w_1920,h_1920/format,webp/quality,q_80&Expires=1798761600&Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvd1lSRk83bzR0d0pXS1ZmV2xJU3BxWS9zYW5kYm94L2RyT0lPa1BwT0ZHN1JVVlRxdVpvTmktaW1nLTNfMTc3MTU4OTM0MzAwMF9uYTFmbl9aVzF3ZEhrdGMzUmhkR1UucG5nP3gtb3NzLXByb2Nlc3M9aW1hZ2UvcmVzaXplLHdfMTkyMCxoXzE5MjAvZm9ybWF0LHdlYnAvcXVhbGl0eSxxXzgwIiwiQ29uZGl0aW9uIjp7IkRhdGVMZXNzVGhhbiI6eyJBV1M6RXBvY2hUaW1lIjoxNzk4NzYxNjAwfX19XX0_&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=HLpgOxmvWtM9c2Y9Sv9pvs32nhhDCb4NrZUySUhlNWVDg1-~MEDobq~-R2EEQ0dJrpxh6Z9odH7T0YPg24tkyCrqb4mBB19kq~qcclVjAysg4SNbHaK99jCkN4Zx7qW2gRI0JdRyFE6iVAhPZ6YSFgtP04EkV60BLIgeWyRWRG0ouXKCSXeiOyRJZfdOBPBtdweUIou7~dbVY7kNTBMjBIzoTpz5UAPQZtqAepFxRx3DZWm1aR9HVjPTXucI2zYlK6hjLNBjflDUdm6MV6xk7nNsutPtAJs0FTN2JSUqXAqGCg0rbVGsrJ1W3f0xykIzgG08KinvgeGmCzIOVhNlug__";

const SUGGESTIONS = [
  {
    icon: Globe,
    title: "搜索信息",
    description: "帮我搜索最新的 AI 行业动态",
    color: "text-blue-400",
    bg: "bg-blue-500/10",
  },
  {
    icon: Code,
    title: "编写代码",
    description: "写一个 Python 爬虫脚本",
    color: "text-emerald-400",
    bg: "bg-emerald-500/10",
  },
  {
    icon: FileText,
    title: "处理文件",
    description: "帮我整理数据并生成报告",
    color: "text-amber-400",
    bg: "bg-amber-500/10",
  },
  {
    icon: Sparkles,
    title: "创意任务",
    description: "帮我写一篇技术博客文章",
    color: "text-purple-400",
    bg: "bg-purple-500/10",
  },
];

interface EmptyStateProps {
  onSuggestionClick: (text: string) => void;
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
        {/* 图片 */}
        <motion.img
          src={EMPTY_STATE_IMG}
          alt="Manus Agent"
          className="w-28 h-28 mx-auto mb-6 rounded-2xl opacity-80"
          animate={{
            y: [0, -6, 0],
          }}
          transition={{
            duration: 4,
            repeat: Infinity,
            ease: "easeInOut",
          }}
        />

        <h2 className="text-xl font-semibold text-foreground mb-2">
          你好，我是 Manus
        </h2>
        <p className="text-sm text-muted-foreground mb-8 leading-relaxed">
          我是一个 AI Agent，可以搜索信息、执行代码、读写文件。
          <br />
          告诉我你想完成什么任务，我会自动规划并执行。
        </p>
      </motion.div>

      {/* 建议卡片 */}
      <motion.div
        initial={{ opacity: 0, y: 20 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ delay: 0.2, duration: 0.4 }}
        className="grid grid-cols-2 gap-3 max-w-lg w-full"
      >
        {SUGGESTIONS.map((s, i) => (
          <motion.button
            key={i}
            onClick={() => onSuggestionClick(s.description)}
            className="glass-subtle rounded-xl p-4 text-left hover:bg-white/5 transition-all duration-200 group"
            whileHover={{ y: -2 }}
            whileTap={{ scale: 0.98 }}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.3 + i * 0.1 }}
          >
            <div className={`p-2 rounded-lg ${s.bg} w-fit mb-2`}>
              <s.icon className={`w-4 h-4 ${s.color}`} />
            </div>
            <p className="text-sm font-medium text-foreground mb-0.5">
              {s.title}
            </p>
            <p className="text-xs text-muted-foreground">
              {s.description}
            </p>
          </motion.button>
        ))}
      </motion.div>
    </div>
  );
}
