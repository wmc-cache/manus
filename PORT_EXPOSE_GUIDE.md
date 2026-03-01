# Manus MVP 端口暴露功能 — 实现指南

## 概述

本功能让 Agent 在 Docker 沙箱中启动 Web 服务后，能生成一个可从宿主机浏览器直接访问的链接，类似 Manus 官方的端口暴露机制。

**核心原理**：通过后端 FastAPI 反向代理，将 `/proxy/{conversation_id}/{port}/{path}` 路径的请求转发到沙箱容器内对应端口的 Web 服务。

---

## 架构图

```
用户浏览器
    │
    ▼
前端 (Vite :3000)  ──proxy──>  后端 (FastAPI :8000)  ──httpx──>  沙箱容器 (:8080)
    │                              │
    │  /proxy/_default/8080/       │  反向代理转发
    │                              │
    └──────────────────────────────┘
```

---

## 修改的文件清单

### 新增文件

| 文件 | 说明 |
|------|------|
| `manus-mvp-backend/backend/sandbox/port_expose.py` | 端口暴露管理器，维护已暴露端口的注册表 |

### 修改的后端文件

| 文件 | 修改内容 |
|------|----------|
| `manus-mvp-backend/backend/main.py` | 新增 `httpx` 导入、`/proxy/` 路径免鉴权、`/api/sandbox/exposed-ports` GET 接口、`/api/sandbox/expose-port` POST 接口、`/proxy/{conversation_id}/{port}/{path}` 反向代理路由 |
| `manus-mvp-backend/backend/agent/tools.py` | 新增 `expose_port` 工具函数和 TOOL_REGISTRY 注册 |
| `manus-mvp-backend/backend/llm/deepseek.py` | 在 TOOLS 列表中添加 `expose_port` 的 function calling schema |
| `manus-mvp-backend/backend/llm/system_prompt.py` | 在系统提示词中添加 `expose_port` 工具描述和使用策略 |

### 修改的前端文件

| 文件 | 修改内容 |
|------|----------|
| `manus-frontend/client/src/hooks/useSandbox.ts` | 新增 `ExposedPort` 接口、`exposedPorts` 状态、`port_exposed` 事件处理 |
| `manus-frontend/client/src/components/sandbox/ComputerPanel.tsx` | 新增 `exposedPorts` prop、已暴露端口链接栏 UI |
| `manus-frontend/client/src/pages/Home.tsx` | 传递 `exposedPorts` prop 给 ComputerPanel |
| `manus-frontend/client/vite.config.ts` | 添加 `/proxy` 路径的代理配置 |

---

## 各文件详细说明

### 1. `sandbox/port_expose.py`（新增）

端口暴露管理器，核心数据结构：

```python
@dataclass
class ExposedPort:
    port: int                 # 端口号
    conversation_id: str      # 会话 ID
    label: str                # 描述标签
    created_at: float         # 创建时间
    internal_host: str        # 容器内部地址（Docker 模式为容器名，进程模式为 localhost）

class PortExposeManager:
    def expose(port, conversation_id, label, internal_host) -> ExposedPort
    def get(port, conversation_id) -> Optional[ExposedPort]
    def list_exposed(conversation_id) -> List[ExposedPort]
    def list_all() -> List[ExposedPort]
    def remove(port, conversation_id) -> bool
    def resolve_internal_host(conversation_id) -> str  # 自动解析容器名
```

**Docker 模式下容器名解析**：`resolve_internal_host()` 会尝试通过 `docker inspect` 获取容器 IP，回退到容器名 `manus-sandbox-{conversation_id}`。

### 2. `main.py` 修改

**关键路由**：

```python
# 免鉴权（在 auth_middleware 中跳过 /proxy/ 路径）
if request.url.path.startswith("/proxy/"):
    return await call_next(request)

# 列出已暴露端口
GET /api/sandbox/exposed-ports?conversation_id=xxx

# 手动注册端口暴露
POST /api/sandbox/expose-port
Body: {"port": 8080, "conversation_id": "_default", "label": "我的网站"}

# 反向代理（核心）
/proxy/{conversation_id}/{port}/{path:path}
# 例如: /proxy/_default/8080/index.html
# 转发到: http://localhost:8080/index.html（进程模式）
# 或: http://manus-sandbox-_default:8080/index.html（Docker 模式）
```

### 3. `tools.py` 修改

新增 `expose_port` 工具，Agent 可在启动 Web 服务后调用：

```python
async def expose_port(port: int, label: str = "") -> str:
    # 1. 解析容器内部地址
    # 2. 注册到 port_expose_manager
    # 3. 发布 port_exposed 事件通知前端
    # 4. 返回可访问的 URL
```

### 4. 前端修改

- **useSandbox.ts**：监听 `port_exposed` WebSocket 事件，维护 `exposedPorts` 状态
- **ComputerPanel.tsx**：在标签栏上方显示已暴露端口的可点击链接
- **vite.config.ts**：将 `/proxy` 路径代理到后端

---

## 使用方式

### 方式一：Agent 自动调用

当用户要求 Agent 创建网页时，Agent 会：

1. 使用 `write_file` 创建 HTML 文件
2. 使用 `shell_exec` 启动 HTTP 服务器：`python3 -m http.server 8080 &`
3. 使用 `expose_port` 暴露端口：`{"port": 8080, "label": "我的网站"}`
4. 前端自动显示可点击的链接

### 方式二：手动 API 调用

```bash
# 注册端口暴露
curl -X POST http://localhost:8000/api/sandbox/expose-port \
  -H "Content-Type: application/json" \
  -d '{"port": 8080, "conversation_id": "_default", "label": "测试网站"}'

# 访问代理
curl http://localhost:8000/proxy/_default/8080/index.html

# 或通过前端代理访问
http://localhost:3000/proxy/_default/8080/index.html
```

---

## Docker 部署注意事项

在 Docker Compose 部署模式下，需要确保：

1. **后端容器**同时连接 `manus-net` 和 `manus-sandbox-net` 网络（已有配置）
2. **沙箱容器**在 `manus-sandbox-net` 网络中（已有配置）
3. `resolve_internal_host()` 会自动解析沙箱容器的网络地址

如果需要从宿主机直接访问（不通过前端），确保后端的 8000 端口已映射到宿主机：

```yaml
# docker-compose.yml
services:
  manus-backend:
    ports:
      - "8000:8000"  # 确保此映射存在
```

---

## 验证测试

```bash
# 1. 启动一个简单的 HTTP 服务
mkdir -p /tmp/test && echo '<html><body><h1>Hello!</h1></body></html>' > /tmp/test/index.html
cd /tmp/test && python3 -m http.server 8080 &

# 2. 注册端口暴露
curl -X POST http://localhost:8000/api/sandbox/expose-port \
  -H "Content-Type: application/json" \
  -d '{"port": 8080, "label": "测试"}'

# 3. 通过代理访问
curl http://localhost:8000/proxy/_default/8080/index.html
# 输出: <html><body><h1>Hello!</h1></body></html>

# 4. 通过前端访问
# 打开浏览器: http://localhost:3000/proxy/_default/8080/index.html
```
