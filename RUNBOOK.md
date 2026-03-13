# Manus AI Agent MVP 运行手册

**版本**: `v0.3.0` (基于 `main` 分支)
**作者**: Manus AI
**日期**: 2026-03-14

---

## 1. 概述

本文档详细说明了如何从零开始在本地环境中部署和运行 **Manus AI Agent MVP** 项目。该项目采用前后端分离架构，并引入了 MCP（Model Context Protocol）微服务来解耦工具执行层，实现了高度模块化和可扩展的 AI Agent 系统。

### 1.1. 系统架构

系统由以下核心组件构成：

| 组件 | 技术栈 | 端口 | 职责 |
|------|--------|------|------|
| **前端** | React 19 + TypeScript + Vite + TailwindCSS | `3000` | 用户交互界面，包含对话、终端、编辑器、浏览器、VNC 面板 |
| **后端** | Python 3.11 + FastAPI + Uvicorn | `8000` | Agent 核心逻辑：对话管理、任务规划、LLM 调用、工具路由 |
| **mcp-filesystem** | Python + FastAPI | `8101` | 文件系统工具服务（read_file, write_file, list_files 等） |
| **mcp-execution** | Python + FastAPI | `8102` | 代码与命令执行服务（shell_exec, execute_code, expose_port） |
| **mcp-research** | Python + FastAPI | `8104` | 搜索与研究服务（web_search, wide_research） |
| **Chromium CDP** | Chromium + Playwright | `9222` | 浏览器自动化，Agent 通过 CDP 协议控制浏览器 |
| **VNC 桌面** | Xvfb + x11vnc + openbox | `5900` | 沙箱虚拟桌面，前端计算机面板实时显示 |

### 1.2. 工具路由策略

Agent 执行工具时采用**混合路由策略**，兼顾模块化与性能：

| 工具类别 | 工具名称 | 执行路径 | 原因 |
|----------|----------|----------|------|
| 文件系统 | read_file, write_file, edit_file, append_file, list_files, find_files, grep_files | MCP → mcp-filesystem:8101 | 无状态，适合微服务化 |
| 代码执行 | shell_exec, execute_code, expose_port | MCP → mcp-execution:8102 | 无状态，适合微服务化 |
| 网页搜索 | web_search | MCP → mcp-research:8104 | 无状态，适合微服务化 |
| 浏览器操作 | browser_navigate, browser_screenshot, browser_click, browser_input, browser_scroll, browser_get_content | **本地 browser_service** | 依赖本地 Playwright 和 Chromium CDP 连接 |
| Agent 上下文 | spawn_sub_agents, wide_research, data_analysis | **本地执行** | 依赖本地 Agent 上下文和 conversation_store |

通过环境变量 `MANUS_USE_MCP=true/false` 可全局切换 MCP 模式与本地模式。

### 1.3. 数据流

```
用户 → 前端(:3000) → SSE/WebSocket → 后端(:8000)
                                         │
                                         ├─ LLM API (Claude/DeepSeek)
                                         │
                                         ├─ MCP 工具路由 ─┬→ mcp-filesystem(:8101)
                                         │                ├→ mcp-execution(:8102)
                                         │                └→ mcp-research(:8104)
                                         │
                                         ├─ 本地 browser_service → Chromium CDP(:9222)
                                         │
                                         └─ VNC WebSocket → x11vnc(:5900) → 前端计算机面板
```

---

## 2. 环境准备

### 2.1. 系统要求

| 项目 | 最低要求 |
|------|----------|
| 操作系统 | Ubuntu 22.04 / Debian 12 / macOS 13+ |
| Python | 3.11+ |
| Node.js | 22.x+ |
| 内存 | 4GB+ |
| 磁盘 | 2GB+ |

### 2.2. 安装系统依赖

**Ubuntu/Debian:**

```bash
sudo apt-get update && sudo apt-get install -y \
  python3.11 python3.11-venv python3-pip \
  nodejs npm \
  git curl \
  chromium-browser \
  xvfb x11vnc openbox
```

**macOS (Homebrew):**

```bash
brew install python@3.11 node git
# macOS 不需要 Xvfb/x11vnc（VNC 桌面功能仅在 Linux 下可用）
# Chromium 通过 Playwright 自动安装
```

### 2.3. 克隆代码

```bash
git clone https://github.com/wmc-cache/manus.git
cd manus
```

---

## 3. 配置

### 3.1. 环境变量（.env）

在 `manus-mvp-backend/backend/` 目录下创建 `.env` 文件：

```env
# ============================================================
# LLM API 配置（必须，至少配置一个）
# ============================================================
CLAUDE_API_KEY=sk-xxx-your-api-key
CLAUDE_BASE_URL=https://api.anthropic.com
CLAUDE_MODEL=claude-sonnet-4-20250514

# ============================================================
# 搜索 API（可选，web_search 工具需要）
# ============================================================
TAVILY_API_KEY=tvly-xxx-your-tavily-key

# ============================================================
# 外部访问地址（可选，expose_port 工具需要）
# ============================================================
# PUBLIC_BASE_URL=https://your-domain.com
```

### 3.2. 运行模式环境变量

以下环境变量在启动后端时通过命令行传入（不写在 `.env` 中）：

| 环境变量 | 默认值 | 说明 |
|----------|--------|------|
| `MANUS_USE_MCP` | `true` | 是否启用 MCP 微服务模式（`false` 则所有工具本地执行） |
| `MANUS_USE_DOCKER` | `false` | 是否使用 Docker 沙箱（`false` 则直接使用本机环境） |
| `DISPLAY` | `:1` | X11 显示器编号，Chromium 和 VNC 使用 |
| `VNC_HOST` | `localhost` | VNC 服务地址 |
| `VNC_PORT` | `5900` | VNC 服务端口 |
| `MCP_FILESYSTEM_URL` | `http://localhost:8101` | mcp-filesystem 服务地址 |
| `MCP_EXECUTION_URL` | `http://localhost:8102` | mcp-execution 服务地址 |
| `MCP_BROWSER_URL` | `http://localhost:8103` | mcp-browser 服务地址（当前未使用，浏览器走本地） |
| `MCP_RESEARCH_URL` | `http://localhost:8104` | mcp-research 服务地址 |
| `MCP_REQUEST_TIMEOUT` | `120` | MCP 服务请求超时时间（秒） |

---

## 4. 安装依赖

### 4.1. 后端 Python 依赖

```bash
# 在项目根目录创建虚拟环境
python3.11 -m venv .venv
source .venv/bin/activate

# 安装后端核心依赖
pip install -r manus-mvp-backend/backend/requirements.txt

# 安装 MCP 服务依赖（共享部分已包含在后端依赖中）
pip install -r mcp-services/mcp-filesystem/requirements.txt
pip install -r mcp-services/mcp-execution/requirements.txt
pip install -r mcp-services/mcp-browser/requirements.txt
pip install -r mcp-services/mcp-research/requirements.txt

# 安装 Playwright 浏览器（Agent 浏览器工具需要）
playwright install chromium
```

**后端核心依赖清单：**

| 包名 | 版本 | 用途 |
|------|------|------|
| `fastapi` | >=0.104.0 | Web 框架 |
| `uvicorn` | >=0.24.0 | ASGI 服务器 |
| `websockets` | >=12.0 | WebSocket 支持（VNC 代理） |
| `openai` | >=1.6.0 | LLM API 客户端 |
| `tavily-python` | >=0.7.0 | 搜索 API |
| `playwright` | >=1.45.0 | 浏览器自动化 |
| `sse-starlette` | >=1.8.0 | SSE 流式响应 |
| `pydantic` | >=2.5.0 | 数据模型验证 |
| `httpx` | >=0.25.0 | MCP 服务间 HTTP 通信 |

### 4.2. 前端 Node.js 依赖

```bash
cd manus-frontend/client
npm install
cd ../..
```

---

## 5. 启动服务

### 方式一：分步手动启动（推荐调试时使用）

按以下顺序在**不同的终端窗口**中依次启动各服务。

#### 步骤 1：启动 VNC 桌面环境（Linux）

```bash
# 启动虚拟显示器
Xvfb :1 -screen 0 1280x800x24 -ac +extension GLX +render -noreset &

# 启动窗口管理器
DISPLAY=:1 openbox &

# 启动 VNC 服务
DISPLAY=:1 x11vnc -forever -nopw -shared -rfbport 5900 &
```

#### 步骤 2：启动 Chromium 浏览器

```bash
DISPLAY=:1 chromium-browser \
  --no-sandbox \
  --disable-gpu \
  --disable-dev-shm-usage \
  --no-first-run \
  --no-default-browser-check \
  --remote-debugging-address=127.0.0.1 \
  --remote-debugging-port=9222 \
  --user-data-dir=/tmp/manus-browser-profile \
  --window-size=1280,800 \
  about:blank &
```

#### 步骤 3：启动 MCP 微服务

```bash
cd mcp-services

# 启动文件系统服务
PYTHONPATH=mcp-shared python3 mcp-filesystem/server.py &

# 启动代码执行服务
PYTHONPATH=mcp-shared python3 mcp-execution/server.py &

# 启动搜索研究服务（需要 TAVILY_API_KEY）
TAVILY_API_KEY=<your-key> PYTHONPATH=mcp-shared python3 mcp-research/server.py &

cd ..
```

#### 步骤 4：启动后端

```bash
cd manus-mvp-backend/backend
source ../../.venv/bin/activate

MANUS_USE_MCP=true \
MANUS_USE_DOCKER=false \
DISPLAY=:1 \
VNC_HOST=localhost \
VNC_PORT=5900 \
MCP_FILESYSTEM_URL=http://localhost:8101 \
MCP_EXECUTION_URL=http://localhost:8102 \
MCP_RESEARCH_URL=http://localhost:8104 \
uvicorn main:app --host 0.0.0.0 --port 8000
```

#### 步骤 5：启动前端

```bash
cd manus-frontend/client
npm run dev
```

### 方式二：使用 start-dev.sh 脚本

项目根目录提供了 `start-dev.sh` 脚本，可一键启动后端和前端（不含 MCP 服务和 VNC）：

```bash
chmod +x start-dev.sh
./start-dev.sh
```

> 该脚本会自动构建前端、启动后端和前端静态服务。MCP 微服务和 VNC 桌面需要按步骤 1-3 手动启动。

### 方式三：Docker Compose 一键部署

```bash
# 构建并启动所有服务（含 MCP 微服务）
docker compose -f docker/docker-compose.mcp.yml up --build -d

# 查看服务状态
docker compose -f docker/docker-compose.mcp.yml ps

# 查看日志
docker compose -f docker/docker-compose.mcp.yml logs -f backend

# 停止所有服务
docker compose -f docker/docker-compose.mcp.yml down
```

---

## 6. 验证服务状态

### 6.1. 健康检查

启动完成后，可通过以下命令验证各服务是否正常运行：

```bash
echo "=== 服务健康检查 ==="

# 前端
curl -s -o /dev/null -w "前端      :3000  HTTP %{http_code}\n" http://localhost:3000

# 后端
curl -s http://localhost:8000/api/health && echo ""

# MCP 服务
for port in 8101 8102 8104; do
  status=$(curl -s -o /dev/null -w "%{http_code}" http://localhost:$port/health)
  echo "MCP 服务  :$port  HTTP $status"
done

# Chromium CDP
curl -s http://127.0.0.1:9222/json/version | python3 -m json.tool | head -3
```

**预期输出：**

```
=== 服务健康检查 ===
前端      :3000  HTTP 200
{"status":"ok","service":"Manus MVP","version":"0.3.0"}
MCP 服务  :8101  HTTP 200
MCP 服务  :8102  HTTP 200
MCP 服务  :8104  HTTP 200
{
    "Browser": "Chrome/128.0.6613.137",
    "Protocol-Version": "1.3",
```

### 6.2. 日志位置

| 服务 | 日志文件 |
|------|----------|
| 后端 | `/tmp/backend.log` 或 `.run/logs/backend.log` |
| 前端 | `.run/logs/frontend.log` |
| mcp-filesystem | `/tmp/mcp-filesystem.log` |
| mcp-execution | `/tmp/mcp-execution.log` |
| mcp-research | `/tmp/mcp-research.log` |
| Chromium | `/tmp/manus-chromium.log` |
| Xvfb | `/tmp/xvfb.log` |
| x11vnc | `/tmp/x11vnc.log` |

---

## 7. 访问与使用

启动所有服务后，打开浏览器访问：

```
http://localhost:3000
```

界面包含以下功能区域：

| 区域 | 说明 |
|------|------|
| **对话面板**（左侧） | 与 Agent 进行自然语言对话，发送任务指令 |
| **计算机面板**（右侧） | 包含终端、编辑器、浏览器三个标签页 |
| **VNC 面板** | 点击右上角"VNC"按钮，实时查看沙箱桌面画面 |

---

## 8. 停止服务

### 手动停止所有服务

```bash
# 停止后端
pkill -f "uvicorn main:app"

# 停止 MCP 服务
pkill -f "mcp-filesystem/server.py"
pkill -f "mcp-execution/server.py"
pkill -f "mcp-research/server.py"

# 停止浏览器和 VNC
pkill -f "chromium.*9222"
pkill -f "x11vnc"
pkill -f "openbox"
pkill -f "Xvfb :1"

# 停止前端
pkill -f "vite"
```

### Docker Compose 停止

```bash
docker compose -f docker/docker-compose.mcp.yml down
```

---

## 9. 常见问题

### Q1: 浏览器工具报"浏览器服务未启动"

**原因**：Chromium 未在本机启动，或 CDP 端口 9222 不可用。

**解决**：
1. 确认 Chromium 已安装：`which chromium-browser`
2. 确认 CDP 端口可用：`curl -s http://127.0.0.1:9222/json/version`
3. 如果端口不通，手动启动 Chromium（参考步骤 2）

### Q2: VNC 计算机面板显示"远程画面已断开"

**原因**：VNC 服务未启动，或后端的 `VNC_HOST`/`VNC_PORT` 配置不正确。

**解决**：
1. 确认 x11vnc 正在运行：`pgrep -a x11vnc`
2. 确认端口 5900 可连接：`python3 -c "import socket; s=socket.create_connection(('127.0.0.1',5900),1); s.close(); print('OK')"`
3. 确认后端启动时设置了 `VNC_HOST=localhost VNC_PORT=5900`

### Q3: MCP 服务连接失败

**原因**：MCP 微服务未启动，或端口被占用。

**解决**：
1. 检查服务健康状态：`curl http://localhost:8101/health`
2. 检查端口占用：`ss -tlnp | grep 810`
3. 查看日志：`cat /tmp/mcp-filesystem.log`
4. 临时回退到本地模式：设置 `MANUS_USE_MCP=false` 重启后端

### Q4: web_search 工具返回空结果

**原因**：未配置 Tavily API Key。

**解决**：在 `.env` 文件中添加 `TAVILY_API_KEY=tvly-xxx`，然后重启后端和 mcp-research 服务。

### Q5: macOS 下 VNC 面板不可用

**原因**：macOS 不支持 Xvfb 虚拟显示器。

**解决**：VNC 桌面功能仅在 Linux 环境下可用。在 macOS 上，浏览器工具仍然可以正常工作（Playwright 会使用 headless 模式），但计算机面板的 VNC 视图将不可用。

---

## 10. 项目文件结构

```
manus/
├── manus-mvp-backend/          # 后端代码
│   └── backend/
│       ├── main.py             # FastAPI 入口
│       ├── .env                # 环境变量配置
│       ├── requirements.txt    # Python 依赖
│       ├── agent/              # Agent 核心逻辑
│       │   ├── core.py         # Agent 主循环
│       │   ├── tools.py        # 原始本地工具执行
│       │   ├── tools_mcp.py    # MCP 工具路由适配器
│       │   └── conversation_store.py
│       ├── llm/                # LLM 调用层
│       │   └── deepseek.py     # LLM API 封装
│       ├── routers/            # API 路由
│       │   ├── chat.py         # 对话 SSE 接口
│       │   ├── websocket.py    # WebSocket 事件推送
│       │   └── vnc.py          # VNC WebSocket 代理
│       ├── sandbox/            # 沙箱环境
│       │   ├── browser.py      # 浏览器服务（Playwright + CDP）
│       │   ├── docker_sandbox.py
│       │   └── event_bus.py    # 事件总线
│       └── config/
│           └── settings.py     # 配置管理
│
├── manus-frontend/             # 前端代码
│   └── client/
│       ├── package.json
│       ├── vite.config.ts
│       └── src/
│           ├── pages/Home.tsx  # 主页面
│           └── hooks/useAgent.ts # Agent 通信 Hook
│
├── mcp-services/               # MCP 微服务
│   ├── mcp-shared/             # 公共库
│   │   ├── mcp_base.py         # MCP 服务基础框架
│   │   └── mcp_client.py       # MCP 客户端（工具路由）
│   ├── mcp-filesystem/         # 文件系统服务
│   │   ├── server.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── mcp-execution/          # 代码执行服务
│   │   ├── server.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   ├── mcp-browser/            # 浏览器服务（Docker 环境用）
│   │   ├── server.py
│   │   ├── requirements.txt
│   │   └── Dockerfile
│   └── mcp-research/           # 搜索研究服务
│       ├── server.py
│       ├── requirements.txt
│       └── Dockerfile
│
├── docker/                     # Docker 配置
│   ├── docker-compose.mcp.yml  # MCP 架构编排文件
│   └── sandbox/Dockerfile
│
├── start-dev.sh                # 开发环境启动脚本
└── RUNBOOK.md                  # 本文档
```
