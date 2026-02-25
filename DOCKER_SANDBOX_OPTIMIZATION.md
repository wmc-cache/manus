# Manus MVP 沙箱容器化优化报告

## 一、优化概述

本次优化将 Manus MVP 项目的沙箱隔离机制从**进程级隔离**升级为**Docker 容器级隔离**，实现了每个会话在独立容器中运行的生产级沙箱能力。这是对标 Manus 1.6 Max VM 级沙箱的关键架构升级。

### 优化前后对比

| 维度 | 优化前（进程级） | 优化后（容器级） | Manus 1.6 Max |
|------|-----------------|-----------------|---------------|
| **隔离级别** | 进程 + chroot | Docker 容器 | VM (gVisor/Firecracker) |
| **文件系统隔离** | 目录级隔离 | 容器级隔离 + Volume 挂载 | 完整 VM 文件系统 |
| **进程隔离** | 无 | PID namespace 隔离 | 完整 VM 进程隔离 |
| **资源限制** | 无 | CPU/内存/PID 配额 | VM 级资源配额 |
| **安全性** | 低（可逃逸） | 中高（capabilities 最小化） | 高（VM 级隔离） |
| **环境一致性** | 依赖宿主机 | 预构建镜像，环境一致 | 预构建 VM 镜像 |
| **休眠/唤醒** | 不支持 | 支持（stop/start） | 支持（VM 快照） |
| **多会话隔离** | 目录隔离 | 完整容器隔离 | 完整 VM 隔离 |

---

## 二、架构设计

### 2.1 整体架构

```
┌─────────────────────────────────────────────────────┐
│                   宿主机 (Host)                      │
│                                                      │
│  ┌──────────────┐    ┌──────────────────────────┐   │
│  │  FastAPI 后端  │───▶│  DockerSandboxManager    │   │
│  │  (main.py)    │    │  (docker_sandbox.py)     │   │
│  └──────┬───────┘    └──────────┬───────────────┘   │
│         │                       │                    │
│         │            ┌──────────▼───────────────┐   │
│         │            │  Docker Tools Adapter     │   │
│         │            │  (docker_tools_adapter.py)│   │
│         │            └──────────┬───────────────┘   │
│         │                       │                    │
│  ┌──────▼───────┐    ┌──────────▼───────────────┐   │
│  │  Monkey Patch │    │     Docker Engine         │   │
│  │  (tools_      │    │                           │   │
│  │   docker_     │    │  ┌─────────┐ ┌─────────┐ │   │
│  │   patch.py)   │    │  │Container│ │Container│ │   │
│  └──────────────┘    │  │ Session1│ │ Session2│ │   │
│                       │  └────┬────┘ └────┬────┘ │   │
│                       └───────┼───────────┼──────┘   │
│                               │           │          │
│  ┌────────────────────────────┼───────────┼──────┐   │
│  │  /tmp/manus_workspace/     │           │      │   │
│  │  ├── session1/ ◄───────────┘           │      │   │
│  │  └── session2/ ◄──────────────────────┘      │   │
│  │  (Volume 挂载，双向同步)                       │   │
│  └───────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

### 2.2 核心组件

| 组件 | 文件 | 职责 |
|------|------|------|
| **DockerSandboxManager** | `sandbox/docker_sandbox.py` | 容器生命周期管理（创建/启动/停止/销毁）、命令执行、文件操作、空闲清理 |
| **Docker Tools Adapter** | `sandbox/docker_tools_adapter.py` | 适配层，将容器操作封装为与原有 tools.py 兼容的接口 |
| **Tools Docker Patch** | `agent/tools_docker_patch.py` | 零侵入猴子补丁，运行时替换原有进程级执行函数 |
| **Docker API** | `sandbox/docker_api.py` | REST API 扩展，提供容器管理的 HTTP 端点 |
| **Sandbox Image** | `docker/sandbox/Dockerfile` | 预构建的沙箱镜像（Ubuntu 22.04 + Python + Node.js + 工具链） |

### 2.3 网络模式自动检测

系统支持三种网络模式，并在启动时自动检测最佳模式：

| 模式 | 适用场景 | 安全性 | 网络能力 |
|------|---------|--------|---------|
| **bridge** | 标准 Docker 环境 | 高（网络隔离） | 容器独立 IP |
| **host** | iptables 受限环境（如嵌套容器） | 中 | 共享宿主机网络 |
| **none** | 纯计算任务 | 最高 | 无网络 |
| **auto**（默认） | 任意环境 | 自适应 | 自动选择最佳 |

---

## 三、新增文件清单

```
manus/
├── docker/
│   ├── sandbox/
│   │   ├── Dockerfile              # 沙箱容器镜像定义
│   │   └── .dockerignore           # Docker 构建忽略规则
│   ├── docker-compose.yml          # 完整服务编排配置
│   └── build-sandbox.sh            # 一键构建脚本
├── manus-mvp-backend/backend/
│   ├── sandbox/
│   │   ├── docker_sandbox.py       # [核心] Docker 沙箱管理器
│   │   ├── docker_tools_adapter.py # [核心] 工具执行适配层
│   │   └── docker_api.py           # REST API 扩展
│   └── agent/
│       └── tools_docker_patch.py   # 零侵入补丁模块
└── tests/
    └── test_docker_sandbox.py      # 集成测试（24 项全通过）
```

---

## 四、安全特性详解

### 4.1 权限最小化

```
--cap-drop ALL                    # 移除所有 Linux capabilities
--cap-add CHOWN                   # 仅保留文件所有权变更
--cap-add DAC_OVERRIDE            # 仅保留文件权限覆盖
--cap-add FOWNER                  # 仅保留文件所有者操作
--cap-add SETUID/SETGID           # 仅保留用户切换（sudo）
--cap-add NET_BIND_SERVICE        # 仅保留低端口绑定
--security-opt no-new-privileges  # 禁止提权
```

### 4.2 资源限制

| 资源 | 默认限制 | 环境变量 |
|------|---------|---------|
| 内存 | 512 MB | `MANUS_CONTAINER_MEM_LIMIT` |
| CPU | 1 核 | `MANUS_CONTAINER_CPU_QUOTA` |
| 进程数 | 256 | `MANUS_CONTAINER_PIDS_LIMIT` |
| 命令超时 | 30 秒 | `MANUS_EXEC_TIMEOUT` |
| 最大超时 | 300 秒 | `MANUS_MAX_EXEC_TIMEOUT` |
| 空闲休眠 | 600 秒 | `MANUS_CONTAINER_IDLE_TIMEOUT` |

### 4.3 环境变量隔离

仅白名单中的环境变量会透传到容器内：

```python
SAFE_ENV_KEYS = {
    "PATH", "HOME", "USER", "LANG", "LC_ALL", "TERM",
    "PYTHONPATH", "NODE_PATH",
}
```

API 密钥（`DEEPSEEK_API_KEY`、`ANTHROPIC_API_KEY` 等）**不会**泄露到沙箱容器中。

---

## 五、使用指南

### 5.1 快速启动

```bash
# 1. 构建沙箱镜像
cd /home/ubuntu/manus
sudo bash docker/build-sandbox.sh

# 2. 设置环境变量
export MANUS_DOCKER_SANDBOX=true
export MANUS_SANDBOX_NETWORK_MODE=auto

# 3. 启动后端（自动应用 Docker 沙箱补丁）
cd manus-mvp-backend/backend
python3 main.py
```

### 5.2 集成到现有代码

在 `main.py` 中添加两行代码即可启用：

```python
# 在 app 创建后添加：
from sandbox.docker_api import register_docker_api
register_docker_api(app)

from agent.tools_docker_patch import apply_docker_sandbox_patch
apply_docker_sandbox_patch()
```

### 5.3 禁用 Docker 沙箱

```bash
export MANUS_DOCKER_SANDBOX=false
# 系统自动回退到原有的进程级隔离
```

### 5.4 管理 API

| 端点 | 方法 | 说明 |
|------|------|------|
| `/api/sandbox/docker/status` | GET | 获取沙箱总体状态 |
| `/api/sandbox/docker/containers` | GET | 列出所有沙箱容器 |
| `/api/sandbox/docker/container/{id}` | GET | 获取指定容器详情 |
| `/api/sandbox/docker/stop/{id}` | POST | 休眠指定容器 |
| `/api/sandbox/docker/start/{id}` | POST | 唤醒指定容器 |
| `/api/sandbox/docker/container/{id}` | DELETE | 销毁指定容器 |

---

## 六、测试结果

全部 **24 项** 集成测试通过：

| 测试类别 | 测试项 | 结果 |
|---------|--------|------|
| 管理器初始化 | 初始化成功 | ✅ |
| 容器创建 | 创建成功 | ✅ |
| 命令执行 | 基本命令执行 | ✅ |
| 命令执行 | 管道命令执行 | ✅ |
| 命令执行 | 用户隔离验证 | ✅ |
| Python 执行 | Python 代码执行 | ✅ |
| Python 执行 | Python 第三方库执行 | ✅ |
| 文件持久化 | 容器内文件创建 | ✅ |
| 文件持久化 | 宿主机文件同步验证 | ✅ |
| 文件持久化 | 宿主机到容器文件同步 | ✅ |
| 会话隔离 | 第二个会话容器创建 | ✅ |
| 会话隔离 | 会话间文件隔离 | ✅ |
| 会话隔离 | workspace 路径隔离 | ✅ |
| 容器生命周期 | 容器休眠 | ✅ |
| 容器生命周期 | 容器唤醒 | ✅ |
| 容器生命周期 | 重启后数据持久化 | ✅ |
| 安全隔离 | 非 root 用户验证 | ✅ |
| 安全隔离 | 宿主机文件系统隔离 | ✅ |
| 安全隔离 | 命令超时保护 | ✅ |
| 适配层 | docker_shell_exec 适配 | ✅ |
| 适配层 | docker_execute_code 适配 | ✅ |
| 适配层 | docker_get_workspace_root 适配 | ✅ |
| 适配层 | 沙箱状态查询 | ✅ |
| 清理 | 容器销毁 | ✅ |

---

## 七、环境变量配置参考

| 环境变量 | 默认值 | 说明 |
|---------|--------|------|
| `MANUS_DOCKER_SANDBOX` | `true` | 是否启用 Docker 沙箱 |
| `MANUS_SANDBOX_IMAGE` | `manus-sandbox:latest` | 沙箱镜像名称 |
| `MANUS_SANDBOX_NETWORK_MODE` | `auto` | 网络模式 (auto/bridge/host/none) |
| `MANUS_SANDBOX_NETWORK` | `manus-sandbox-net` | bridge 模式网络名称 |
| `MANUS_HOST_WORKSPACE_BASE` | `/tmp/manus_workspace` | 宿主机 workspace 根目录 |
| `MANUS_CONTAINER_MEM_LIMIT` | `512m` | 容器内存限制 |
| `MANUS_CONTAINER_CPU_QUOTA` | `100000` | CPU 配额（微秒） |
| `MANUS_CONTAINER_PIDS_LIMIT` | `256` | 最大进程数 |
| `MANUS_EXEC_TIMEOUT` | `30` | 默认命令超时（秒） |
| `MANUS_MAX_EXEC_TIMEOUT` | `300` | 最大命令超时（秒） |
| `MANUS_CONTAINER_IDLE_TIMEOUT` | `600` | 空闲自动休眠时间（秒） |

---

## 八、后续优化方向

1. **升级到 gVisor/Firecracker**：进一步提升到 VM 级隔离，对标 Manus 1.6 Max
2. **容器快照**：支持容器状态快照和恢复，加速唤醒
3. **镜像预热池**：预创建待命容器，减少首次创建延迟
4. **网络策略**：实现细粒度出站流量控制（白名单域名）
5. **磁盘配额**：通过 Docker storage driver 限制单容器磁盘使用
6. **监控告警**：容器资源使用监控和异常告警
