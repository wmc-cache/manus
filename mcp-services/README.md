# Manus MCP 工具微服务

本目录包含将 Manus MVP 工具层拆分后的独立 MCP（Model Context Protocol）微服务。

## 架构概览

```
manus-mvp-backend (Agent 核心)
        |
        | HTTP (MCP 协议)
        |
  ┌─────┴──────────────────────────────┐
  │                                    │
  ▼                                    ▼
mcp-filesystem (:8101)        mcp-execution (:8102)
  - read_file                   - shell_exec
  - write_file                  - execute_code
  - edit_file                   - expose_port
  - append_file
  - list_files                mcp-browser (:8103)
  - find_files                  - browser_navigate
  - grep_files                  - browser_screenshot
                                - browser_get_content
mcp-research (:8104)            - browser_click
  - web_search                  - browser_input
  - wide_research               - browser_scroll
  - spawn_sub_agents
  - data_analysis
```

## 服务说明

| 服务 | 端口 | 职责 |
|------|------|------|
| `mcp-filesystem` | 8101 | 工作区文件的读写、编辑、搜索 |
| `mcp-execution`  | 8102 | shell 命令执行、Python 代码运行、端口暴露 |
| `mcp-browser`    | 8103 | 浏览器自动化（Playwright） |
| `mcp-research`   | 8104 | 网页搜索（Tavily）、批量研究、数据分析 |

## 快速启动

### 方式一：Docker Compose（推荐）

```bash
# 从项目根目录执行
cp manus-mvp-backend/backend/.env .env  # 复制环境变量
docker compose -f docker/docker-compose.mcp.yml up -d
```

### 方式二：本地直接运行

```bash
# 安装依赖
pip install fastapi uvicorn pydantic httpx tavily-python

# 启动各服务（每个服务在独立终端中运行）
cd mcp-services

PYTHONPATH=mcp-shared python3 mcp-filesystem/server.py
PYTHONPATH=mcp-shared python3 mcp-execution/server.py
PYTHONPATH=mcp-shared python3 mcp-research/server.py
# mcp-browser 需要额外安装 playwright
pip install playwright && playwright install chromium
PYTHONPATH=mcp-shared python3 mcp-browser/server.py
```

### 启用 MCP 模式

在 `manus-mvp-backend/backend/.env` 中添加：

```env
# 启用 MCP 模式
MANUS_USE_MCP=true

# MCP 服务地址（本地开发）
MCP_FILESYSTEM_URL=http://localhost:8101
MCP_EXECUTION_URL=http://localhost:8102
MCP_BROWSER_URL=http://localhost:8103
MCP_RESEARCH_URL=http://localhost:8104
```

## MCP 协议接口

每个服务均暴露以下标准端点：

| 端点 | 方法 | 说明 |
|------|------|------|
| `/.well-known/mcp.json` | GET | 服务发现，返回服务信息和工具定义 |
| `/tools` | GET | 列出所有可用工具 |
| `/execute` | POST | 执行指定工具 |
| `/health` | GET | 健康检查 |

### 执行工具示例

```bash
# 写入文件
curl -X POST http://localhost:8101/execute \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "write_file",
    "arguments": {"path": "hello.txt", "content": "Hello World"},
    "conversation_id": "my_session"
  }'

# 执行代码
curl -X POST http://localhost:8102/execute \
  -H "Content-Type: application/json" \
  -d '{
    "tool_name": "execute_code",
    "arguments": {"code": "print(1+1)"},
    "conversation_id": "my_session"
  }'
```

## 降级策略

若 MCP 服务不可用，可通过设置 `MANUS_USE_MCP=false` 回退到原有的本地工具执行模式，无需修改任何代码。

## 目录结构

```
mcp-services/
├── README.md
├── mcp-shared/          # 公共基础库
│   ├── mcp_base.py      # MCPService 基类、数据模型
│   └── mcp_client.py    # Agent 侧 MCP 客户端
├── mcp-filesystem/      # 文件系统服务
│   ├── server.py
│   ├── requirements.txt
│   └── Dockerfile
├── mcp-execution/       # 代码执行服务
│   ├── server.py
│   ├── requirements.txt
│   └── Dockerfile
├── mcp-browser/         # 浏览器操作服务
│   ├── server.py
│   ├── requirements.txt
│   └── Dockerfile
└── mcp-research/        # 搜索研究服务
    ├── server.py
    ├── requirements.txt
    └── Dockerfile
```
