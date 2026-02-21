# Manus MVP — AI Agent 系统

一个基于 Web 的 AI Agent 系统 MVP，支持对话交互、工具调用（搜索、代码执行、文件操作），使用 React 前端 + FastAPI 后端 + DeepSeek API。

## 系统架构

```
┌─────────────────────────────────────────────────┐
│                  React Frontend                  │
│  (对话界面 / 工具调用可视化 / 实时 SSE 交互)      │
└──────────────────────┬──────────────────────────┘

                       │ SSE (Server-Sent Events)
                       ▼
┌─────────────────────────────────────────────────┐
│                FastAPI Backend                  │
│  ┌──────────┐  ┌──────────┐  ┌──────────────┐   │
│  │  Agent    │  │  LLM     │  │   Tools      │  │
│  │  Engine   │──│  Client  │──│  (搜索/代码/  │  │
│  │  (Loop)   │  │(DeepSeek)│  │   文件操作)   │  │
│  └──────────┘  └──────────┘  └──────────────┘   │
└─────────────────────────────────────────────────┘
```

## 技术栈

| 层级 | 技术 | 说明 |
|------|------|------|
| 前端 | React 19 + TypeScript + TailwindCSS 4 | 毛玻璃工作台设计风格 |
| 后端 | FastAPI + Python 3.11 | 异步 Agent 引擎 |
| LLM | DeepSeek API (OpenAI 兼容) | 支持 Function Calling |
| 通信 | SSE (Server-Sent Events) | 实时流式输出 |

## 核心功能

### Agent 工具系统

| 工具 | 功能 | 说明 |
|------|------|------|
| `web_search` | 网页搜索 | 通过 DuckDuckGo 搜索互联网 |
| `wide_research` | 并行研究 | 对一组对象并发搜索并落盘 `research/summary.md` |
| `shell_exec` | 终端命令 | 在工作目录执行 shell 命令 |
| `execute_code` | 代码执行 | 在沙箱中执行 Python 代码 |
| `read_file` | 读取文件 | 读取指定路径的文件内容 |
| `write_file` | 写入文件 | 创建或写入文件 |

### Agent Loop 工作流程

1. 用户输入消息
2. Agent 分析任务，决定是否需要调用工具
3. 如需工具：调用工具 → 获取结果 → 反馈给 LLM → 继续推理
4. 循环直到 LLM 给出最终回答（最多 10 轮迭代）
5. 全程通过 SSE 实时推送状态给前端

## 快速开始

### 1. 启动后端

```bash
cd backend
pip install -r requirements.txt
export DEEPSEEK_API_KEY="your-api-key"
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

### 2. 启动前端

```bash
cd ../manus-frontend
pnpm install
pnpm dev
```

### 3. 访问

- 前端: http://localhost:3000
- 后端 API: http://localhost:8000
- API 文档: http://localhost:8000/docs

## API 接口

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | `/api/health` | 健康检查 |
| POST | `/api/chat` | 发送消息（SSE 流式响应） |
| GET | `/api/conversations` | 获取对话列表 |
| GET | `/api/conversations/{id}` | 获取对话详情 |

### SSE 事件类型

| 事件 | 说明 |
|------|------|
| `thinking` | Agent 正在思考 |
| `content` | 文本内容输出 |
| `tool_call` | 工具调用开始 |
| `tool_result` | 工具调用结果 |
| `done` | 完成 |
| `error` | 错误 |

## 项目结构

```
manus-mvp/
├── backend/
│   ├── main.py              # FastAPI 入口
│   ├── agent/
│   │   ├── core.py          # Agent 核心引擎
│   │   └── tools.py         # 工具定义与执行
│   ├── llm/
│   │   └── deepseek.py      # DeepSeek API 封装
│   ├── models/
│   │   └── schemas.py       # 数据模型
│   └── requirements.txt
│
└── manus-frontend/          # React 前端项目
    └── client/src/
        ├── pages/Home.tsx    # 主页面
        ├── components/
        │   ├── ChatInput.tsx
        │   ├── EmptyState.tsx
        │   ├── MessageBubble.tsx
        │   ├── Sidebar.tsx
        │   ├── ThinkingIndicator.tsx
        │   └── ToolCallCard.tsx
        ├── hooks/useAgent.ts # Agent SSE 连接
        └── types/index.ts   # 类型定义
```

## 环境变量

| 变量 | 说明 | 必填 |
|------|------|------|
| `DEEPSEEK_API_KEY` | DeepSeek API 密钥 | 是 |
| `MANUS_ALLOWED_ORIGINS` | 后端允许的前端来源（逗号分隔，默认仅本机 3000） | 否 |
| `MANUS_API_TOKEN` | 后端 API 鉴权令牌（启用后 HTTP/WS 需携带） | 否 |
| `VITE_MANUS_API_TOKEN` | 前端请求时附带的鉴权令牌（应与 `MANUS_API_TOKEN` 一致） | 否 |
| `MANUS_SANDBOX_INHERIT_ENV` | 是否让工具子进程继承全部环境变量（默认否，仅保留安全变量） | 否 |
| `MANUS_WIDE_RESEARCH_MAX_ITEMS` | `wide_research` 单次最大条目数（默认 20） | 否 |
| `MANUS_WIDE_RESEARCH_CONCURRENCY` | `wide_research` 并发度（默认 5） | 否 |

## License

MIT
