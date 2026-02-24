# Manus MVP 第二轮深度优化实施报告

## 概述

本轮优化基于对项目前后端代码的全面审计和对 Manus 1.6 Max 的深入研究，按 P0-P2 优先级实施了 8 项关键优化，覆盖性能、架构、智能化和用户体验四个维度。

---

## 一、P0 优化：文件轮询风暴修复

### 问题诊断

后端日志显示 `/api/sandbox/files` 被前端高频轮询，同一对话在数秒内产生 20+ 次重复请求。根本原因是 `useSandbox.ts` 中每次收到 `file_changed` 或 `file_opened` WebSocket 事件都会立即调用 `fetchFileTree()`，而 Agent 快速连续操作文件时会触发大量事件。

### 修复方案

在 `useSandbox.ts` 中引入 **debounce 防抖机制**，将 `fetchFileTree` 的调用频率限制为每 500ms 最多一次。同时增加了 `lastFetchRef` 时间戳检查，确保短时间内不会重复发起相同请求。

| 指标 | 修复前 | 修复后 |
|------|--------|--------|
| 文件树请求频率 | 每次事件立即请求 | 500ms 防抖合并 |
| 10 次快速文件操作 | 10+ 次 API 请求 | 1-2 次 API 请求 |
| 服务器负载 | 高 | 低 |

**修改文件**：`manus-frontend/client/src/hooks/useSandbox.ts`

---

## 二、P1 优化：启用并行工具执行

### 问题诊断

`core.py` 中的工具执行循环每轮只处理 LLM 返回的第一个工具调用，其余全部跳过。当 LLM 同时返回多个独立的只读操作（如同时读取两个文件）时，效率极低。

### 修复方案

引入 **安全工具白名单** 机制，将 `read_file`、`list_files`、`find_files`、`grep_files`、`web_search` 等只读工具标记为可并行执行。当 LLM 返回多个工具调用且全部属于安全工具时，使用 `asyncio.gather` 并行执行；否则仍按顺序执行第一个。

```python
SAFE_PARALLEL_TOOLS = {
    "read_file", "list_files", "find_files", "grep_files",
    "web_search", "browser_get_content", "browser_screenshot",
}
```

**修改文件**：`manus-mvp-backend/backend/agent/core.py`

---

## 三、P1 优化：重构计划推进逻辑

### 问题诊断

`_transition_plan_to_execution` 方法中存在硬编码限制：只有当 `current_phase_id == 1` 时才会自动推进到下一阶段。这意味着后续阶段的推进完全依赖 LLM 的自觉性，容易导致计划卡死在某个阶段。

### 修复方案

移除硬编码的阶段限制，改为基于迭代次数的智能推进策略。每个阶段允许的最大迭代次数为 `max(5, MAX_ITERATIONS // phase_count)`，超过后自动推进到下一阶段。同时在推进时注入提示消息，引导 LLM 意识到阶段变化。

**修改文件**：`manus-mvp-backend/backend/agent/core.py`

---

## 四、P1 优化：引入 Tokenizer 精确上下文控制

### 问题诊断

原有的上下文截断基于粗略的字符计数（`len(text)`），无法精确控制 Token 预算。中英文混合内容下，字符数与 Token 数的比例差异很大，可能导致上下文溢出或浪费。

### 修复方案

创建 `llm/tokenizer.py` 模块，基于 `tiktoken` 库（使用 `cl100k_base` 编码器）提供精确的 Token 计数能力。在 `context_manager.py` 的 `build_messages` 方法末尾增加 Token 预算检查：当总 Token 数超过预算时，从中间删除最旧的消息，保留首尾消息。

```python
# 核心 API
count_tokens(text: str) -> int
count_messages_tokens(messages: List[dict]) -> int
truncate_to_token_budget(text: str, max_tokens: int) -> str
estimate_remaining_budget(messages: List[dict], max_tokens: int) -> int
```

| 参数 | 默认值 | 环境变量 |
|------|--------|----------|
| 最大上下文 Token | 60,000 | `MANUS_MAX_CONTEXT_TOKENS` |
| 预留响应 Token | 4,000 | `MANUS_RESERVED_RESPONSE_TOKENS` |

**新增文件**：`manus-mvp-backend/backend/llm/tokenizer.py`
**修改文件**：`manus-mvp-backend/backend/agent/context_manager.py`

---

## 五、P1 优化：引入代码语法高亮

### 问题诊断

Agent 输出的代码块无语法高亮，所有代码以纯白色文本展示，可读性差。Manus 1.6 Max 的代码块有完整的语法高亮、语言标签和一键复制功能。

### 修复方案

引入 `react-syntax-highlighter` 库，创建两个新组件：

**CodeBlock 组件**：提供代码语法高亮、语言标签、行号和一键复制功能，使用 VS Code Dark+ 主题。

**MarkdownRenderer 组件**：替代原有的 `Streamdown`，将 Markdown 内容解析为文本段和代码块段，代码块段使用 `CodeBlock` 渲染。同时支持表格、列表、引用、链接等 Markdown 元素的增强渲染。

**新增文件**：
- `manus-frontend/client/src/components/CodeBlock.tsx`
- `manus-frontend/client/src/components/MarkdownRenderer.tsx`

**修改文件**：`manus-frontend/client/src/components/MessageBubble.tsx`

---

## 六、P2 优化：增强循环检测

### 问题诊断

原有的循环检测仅检查完全相同的工具签名（工具名 + 参数 JSON）。但实际中存在"同一工具、略有不同参数"的循环模式（如反复读取同一文件但行号范围不同），这种模式无法被检测到。

### 修复方案

在原有的完全签名匹配基础上，增加**同工具名重复检测**：当近期窗口内同一工具被调用超过 `threshold + 1` 次时，即使参数不同也视为循环并阻断。

**修改文件**：`manus-mvp-backend/backend/agent/core.py`（`_is_repeated_tool_signature` 方法）

---

## 七、P2 优化：本地化静态资源

### 问题诊断

`EmptyState.tsx` 中的空状态图片使用了 `manuscdn.com` 的外部 CDN URL，该 URL 包含签名和过期时间，在外部环境中大概率加载失败。

### 修复方案

将外部 CDN 图片替换为 **SVG 内联图标**，使用渐变色闪电符号作为 Manus Logo，完全不依赖外部资源。同时将建议卡片从 4 个扩展为 6 个（新增"数据分析"和"自动化"），布局从 2x2 改为 3x2。

**修改文件**：`manus-frontend/client/src/components/EmptyState.tsx`

---

## 八、计算机面板当前工具指示（前轮已完成，本轮验证）

计算机面板顶部的工具活动指示条正常工作，能够显示当前 Agent 正在使用的工具类型和关键参数。标签页自动切换和活跃脉冲指示也已验证通过。

---

## 修改文件汇总

| 文件 | 类型 | 优化项 |
|------|------|--------|
| `backend/llm/tokenizer.py` | 新增 | Tokenizer 模块 |
| `backend/agent/context_manager.py` | 修改 | Token 预算检查 |
| `backend/agent/core.py` | 修改 | 并行工具执行 + 计划推进 + 循环检测 |
| `frontend/src/hooks/useSandbox.ts` | 修改 | 防抖机制 |
| `frontend/src/components/CodeBlock.tsx` | 新增 | 代码高亮组件 |
| `frontend/src/components/MarkdownRenderer.tsx` | 新增 | 增强 Markdown 渲染 |
| `frontend/src/components/MessageBubble.tsx` | 修改 | 使用新渲染器 |
| `frontend/src/components/EmptyState.tsx` | 修改 | 本地化 + 扩展建议 |

---

## 服务状态

本轮所有优化已部署并验证通过：

- 后端 (8000)：所有模块导入测试通过，API 健康检查正常
- 前端 (3000)：编译无错误，界面渲染正常
- 前端访问地址：`https://3000-i9buml8j1xhlrbs0pgddh-09cbfa93.sg1.manus.computer`
