# Manus MVP 优化报告 — 向 Manus 1.6 Max 对齐

## 概述

本次优化基于对 `wmc-cache/manus` 开源 MVP 项目与 Manus 1.6 Max 的全面对比分析，系统性地改进了后端架构、Agent 引擎、工具集、前端交互等多个维度。优化目标是使 MVP 项目在架构设计和功能能力上更接近 Manus 1.6 Max 的水平。

---

## 一、优化清单总览

| 模块 | 优化项 | 文件 | 状态 |
|------|--------|------|------|
| System Prompt | 增强版精细指令体系 | `llm/system_prompt.py` | ✅ 完成 |
| 上下文工程 | KV-cache 友好压缩、错误记忆、注意力操纵 | `agent/context_manager.py` | ✅ 完成 |
| 规划系统 | 动态计划修订、阶段能力标注、LLM 驱动调整 | `agent/planner.py` | ✅ 完成 |
| 模型路由 | 增强版多模型路由逻辑 | `agent/model_router.py` | ✅ 完成 |
| 工具集扩展 | 新增 find_files、grep_files | `agent/tools_search.py` | ✅ 完成 |
| 工具注册 | 搜索工具注册到 TOOL_REGISTRY | `agent/tools.py` | ✅ 完成 |
| LLM 工具定义 | 新增 find_files、grep_files 工具定义 | `llm/deepseek.py` | ✅ 完成 |
| 多智能体 | ETA 估算、输出模式验证、优雅降级 | `agent/parallel_enhanced.py` | ✅ 完成 |
| 数据模型 | PlanPhase 添加 capabilities 字段 | `models/schemas.py` | ✅ 完成 |
| 前端类型 | 新增工具名称/图标映射 | `types/index.ts` | ✅ 完成 |
| 前端组件 | ToolCallCard 支持所有新工具 | `components/ToolCallCard.tsx` | ✅ 完成 |
| Agent 引擎 | DEFAULT_TOOL_NAMES 添加新工具 | `agent/core.py` | ✅ 完成 |

---

## 二、各模块优化详情

### 2.1 增强版 System Prompt（`llm/system_prompt.py`）

**对标 Manus 1.6 Max 特性**：Manus 1.6 Max 使用精心设计的 XML 结构化 System Prompt，包含 `<agent_loop>`、`<tool_use>`、`<error_handling>` 等分区，指令极为精细。

**优化内容**：
- 创建独立的 `system_prompt.py` 模块，将 System Prompt 从 `deepseek.py` 中解耦
- 采用 XML 风格的结构化分区：`<agent_loop>`、`<tool_use>`、`<error_handling>`、`<file_operations>`、`<output_format>`
- 引入 **Agent Loop 七步法**：分析上下文 → 思考 → 选择工具 → 执行 → 观察 → 迭代 → 交付
- 增加 **三次失败规则**：同一操作最多重试三次后必须换方案
- 增加 **文件操作检查点策略**：每两次滚动/浏览操作后必须保存关键信息
- 增加 **输出格式规范**：段落与表格交替、粗体强调、引用块、行内引用
- **KV-cache 友好**：System Prompt 在多轮对话中保持稳定，不做动态注入

### 2.2 上下文工程增强（`agent/context_manager.py`）

**对标 Manus 1.6 Max 特性**：Manus 1.6 Max 使用 KV-cache 友好的上下文压缩策略，只在尾部追加/裁剪，不修改前缀。还实现了 todo.md 注意力操纵和 `<compacted_history>` 标记。

**优化内容**：
- **KV-cache 友好压缩**：只在消息序列尾部进行追加/裁剪操作，避免修改前缀导致 KV-cache 失效
- **错误记忆保留**：失败的工具调用在压缩时享有更高优先级，保留在上下文中以避免重复犯错
- **`<compacted_history>` 标记**：被压缩的旧消息不是简单丢弃，而是生成结构化摘要标记，保留关键操作历史
- **文件外部化**：超长工具结果自动写入文件，上下文中只保留文件路径引用
- **LLM 摘要压缩**：可选使用 LLM 对长消息进行智能摘要（通过环境变量 `MANUS_CONTEXT_LLM_SUMMARY` 启用）
- **动态工具门控**：根据当前计划阶段的 `capabilities` 字段动态筛选可用工具
- **Reasoning Effort 注入**：根据任务复杂度动态调整思考深度提示
- **Token 预算管理**：可配置的上下文大小限制，支持多级裁剪策略

### 2.3 规划系统增强（`agent/planner.py`）

**对标 Manus 1.6 Max 特性**：Manus 1.6 Max 的计划系统支持 `update`（创建/修订）和 `advance`（推进阶段）两种操作，每个阶段可标注所需能力。

**优化内容**：
- **动态计划修订**：支持在执行过程中根据新发现的信息修订计划（`revise_plan` 方法）
- **阶段能力标注**：每个 Phase 可标注 `capabilities`（如 `web_development`、`data_analysis`、`deep_research`），用于工具门控
- **LLM 驱动的计划调整**：当任务偏离原计划时，可调用 LLM 重新生成计划
- **阶段复杂度自适应**：简单任务 2 个阶段，典型任务 4-6 个，复杂任务 10+ 个
- **计划状态追踪**：支持 pending → running → completed/failed 的完整生命周期
- **向后兼容**：保持与原有 `create_plan` / `advance_phase` 接口的兼容性

### 2.4 工具集扩展（`agent/tools_search.py`）

**对标 Manus 1.6 Max 特性**：Manus 1.6 Max 拥有 `match` 工具，支持 `glob`（文件名匹配）和 `grep`（内容搜索）两种模式。

**新增工具**：

#### `find_files` — 文件查找
- 使用 glob 模式匹配查找文件
- 支持 `**` 递归匹配
- 结果按修改时间倒序排列
- 可配置最大返回数量
- 自动排除 `node_modules`、`.git`、`__pycache__` 等目录

#### `grep_files` — 内容搜索
- 使用正则表达式搜索文件内容
- 支持上下文行显示（leading/trailing）
- 结果按文件修改时间倒序排列
- 自动跳过二进制文件
- 可配置搜索范围（scope glob 模式）

### 2.5 多智能体并行处理增强（`agent/parallel_enhanced.py`）

**对标 Manus 1.6 Max 特性**：Manus 1.6 Max 的 `map` 工具支持最多 2000 个并行子任务，每个子任务在独立沙箱中运行。

**优化内容**：
- **ETA 估算**：基于已完成任务的平均耗时，实时估算剩余时间
- **输出模式验证**：支持 `output_schema` 定义，验证子代理输出是否符合预期结构
- **优雅降级**：即使部分子代理失败，也始终返回所有结果（包括部分结果）
- **增强进度报告**：包含已用时间、ETA、成功率等详细信息
- **异常隔离**：使用 `return_exceptions=True` 确保单个子代理的异常不会影响其他子代理
- **持续时间追踪**：记录每个子代理的执行时长，用于性能分析

### 2.6 LLM 集成优化（`llm/deepseek.py`）

**优化内容**：
- **System Prompt 解耦**：从独立模块导入增强版 System Prompt，支持热替换
- **工具定义扩展**：新增 `find_files` 和 `grep_files` 的 OpenAI Function Calling 格式定义
- **总计 18 个工具定义**：覆盖搜索、终端、浏览器、文件操作、数据分析等全场景

### 2.7 前端优化

**优化内容**：
- **工具名称映射扩展**：新增 9 个工具的中文名称映射（browser_click、browser_input、browser_scroll、edit_file、append_file、list_files、find_files、grep_files、data_analysis）
- **图标映射扩展**：为所有新工具分配了语义化的 Lucide 图标
- **ToolCallCard 增强**：为所有新工具添加了参数格式化显示逻辑
- **PlanPhase 类型扩展**：添加 `capabilities` 可选字段支持

---

## 三、与 Manus 1.6 Max 的差距缩小情况

| 维度 | 优化前差距 | 优化后差距 | 改善程度 |
|------|-----------|-----------|---------|
| System Prompt 精细度 | 大（简单列表式） | 小（XML 结构化） | 显著改善 |
| 上下文工程 | 大（简单截断） | 中（KV-cache 友好+摘要） | 显著改善 |
| 规划系统 | 大（静态计划） | 小（动态修订+能力标注） | 显著改善 |
| 工具集完备性 | 大（缺少文件搜索） | 中（新增 find/grep） | 明显改善 |
| 多智能体 | 中（基本并行） | 小（ETA+优雅降级） | 明显改善 |
| 前端工具展示 | 中（部分工具无图标） | 小（全覆盖） | 明显改善 |

### 仍存在的差距（需后续优化）

1. **沙箱隔离**：Manus 1.6 Max 使用 Docker 级别的沙箱隔离，MVP 仍使用进程级隔离
2. **浏览器自动化**：Manus 1.6 Max 使用 Playwright 实现完整的浏览器自动化（截图、元素索引），MVP 使用 Selenium 且功能有限
3. **多模型支持**：Manus 1.6 Max 支持 GPT-4.1、Gemini 等多模型，MVP 仅支持 DeepSeek
4. **图片/视频/音频生成**：Manus 1.6 Max 支持多模态生成，MVP 暂不支持
5. **PPT/Slides 生成**：Manus 1.6 Max 有专门的 Slides 模式，MVP 暂不支持
6. **Web 应用脚手架**：Manus 1.6 Max 可一键初始化 React/Expo 项目，MVP 暂不支持
7. **定时任务**：Manus 1.6 Max 支持 cron/interval 定时任务调度，MVP 暂不支持

---

## 四、修改的文件清单

### 后端（`manus-mvp-backend/backend/`）

| 文件 | 操作 | 说明 |
|------|------|------|
| `llm/system_prompt.py` | 新建 | 增强版 System Prompt 模块 |
| `llm/deepseek.py` | 修改 | 引用增强 Prompt + 新增工具定义 |
| `agent/context_manager.py` | 重写 | 增强版上下文工程 |
| `agent/planner.py` | 重写 | 增强版规划系统 |
| `agent/tools_search.py` | 新建 | find_files + grep_files 工具 |
| `agent/tools.py` | 修改 | 注册搜索工具 |
| `agent/parallel_enhanced.py` | 重写 | 增强版并行处理 |
| `agent/core.py` | 修改 | DEFAULT_TOOL_NAMES 添加新工具 |
| `models/schemas.py` | 修改 | PlanPhase 添加 capabilities |

### 前端（`manus-frontend/client/src/`）

| 文件 | 操作 | 说明 |
|------|------|------|
| `types/index.ts` | 修改 | 新增工具映射 + PlanPhase capabilities |
| `components/ToolCallCard.tsx` | 重写 | 支持所有新工具的图标和参数展示 |

---

## 五、验证结果

- **模块导入测试**：全部 7 个核心模块导入成功
- **工具注册验证**：19 个工具全部注册到 TOOL_REGISTRY
- **LLM 工具定义**：18 个工具定义通过验证
- **后端健康检查**：`/api/health` 返回 `{"status":"ok"}`
- **前端编译**：Vite 编译成功，无 TypeScript 错误
- **服务可用性**：前端 (3000) 和后端 (8000) 均正常响应

---

## 六、使用说明

### 访问链接
- **前端界面**：https://3000-i9buml8j1xhlrbs0pgddh-09cbfa93.sg1.manus.computer
- **后端 API 文档**：https://8000-i9buml8j1xhlrbs0pgddh-09cbfa93.sg1.manus.computer/docs

### 环境变量配置

新增的可配置环境变量：

| 变量名 | 默认值 | 说明 |
|--------|--------|------|
| `MANUS_ENHANCED_PROMPT` | `true` | 是否启用增强版 System Prompt |
| `MANUS_CONTEXT_LLM_SUMMARY` | `false` | 是否启用 LLM 摘要压缩 |
| `MANUS_CONTEXT_EXTERNALIZE_THRESHOLD` | `1000` | 工具结果外部化阈值（字符数） |
| `MANUS_ERROR_RETENTION_WINDOW` | `10` | 错误记忆保留窗口（消息数） |
| `MANUS_CONTEXT_PLAN_MAX_CHARS` | `3000` | 计划注入最大字符数 |
