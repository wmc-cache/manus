# wmc-cache/manus 开源项目 与 Manus 1.6 Max 深度对比分析报告

**分析日期**：2025 年 2 月 25 日

---

## 一、概述

本报告对 GitHub 上的 [wmc-cache/manus](https://github.com/wmc-cache/manus) 开源 MVP 项目（以下简称"MVP 项目"）与 Manus 官方的 **Manus 1.6 Max**（以下简称"Max"）进行全面的技术对比分析。通过深入阅读 MVP 项目的全部后端和前端源代码，并结合 Manus 官方博客、泄露的系统提示词、以及多篇技术分析文章中披露的 Max 架构信息，本报告从架构设计、上下文工程、规划系统、工具体系、多智能体协作、沙箱环境、前端体验等七个维度展开对比，识别两者之间的核心差异，并评估 MVP 项目已实施的优化点及仍存在的差距。

---

## 二、项目定位与技术栈对比

MVP 项目是一个面向学习和二次开发的开源复刻版本，试图在架构层面模仿 Manus 1.6 Max 的核心设计理念。两者在技术栈选择上存在显著差异，这些差异直接影响了系统的能力边界。

| 维度 | wmc-cache/manus (MVP) | Manus 1.6 Max |
| --- | --- | --- |
| **定位** | 开源学习/二次开发 MVP | 商业级生产系统 |
| **后端框架** | FastAPI (Python) | 分布式微服务架构 |
| **默认 LLM** | DeepSeek Chat（单模型） | Claude 3.7 Sonnet + Qwen 系列（多模型路由） |
| **前端框架** | React 19 + Vite + TypeScript | React + 自定义渲染引擎 |
| **沙箱隔离** | 进程级隔离（子进程 + 工作目录） | Docker/microVM 级隔离（每任务独立 Ubuntu VM） |
| **浏览器自动化** | Selenium（基础功能） | Playwright（完整自动化 + 元素索引 + 截图标注） |
| **搜索引擎** | Tavily API / DuckDuckGo 回退 | 自建搜索基础设施 |
| **部署方式** | 单机 `nohup` 启动 | 云原生分布式部署 |

---

## 三、核心架构对比

### 3.1 Agent Loop（代理循环）

**Manus 1.6 Max** 的 Agent Loop 采用严格的六步迭代模型，其核心设计哲学是"每次响应仅包含一个工具调用"，通过事件流（Event Stream）驱动状态推进。根据泄露的系统提示词，Max 的循环包括：分析事件、选择工具、等待执行、迭代、提交结果、进入待命。这种设计确保了在平均约 50 次工具调用的复杂任务中保持高度的可控性和可追溯性。

**MVP 项目** 在 `agent/core.py` 中实现了类似的循环结构，但存在几个关键差异。首先，MVP 的循环上限为 `MAX_ITERATIONS = 25`（可配置），远低于 Max 在实际任务中展现的 50+ 次调用能力。其次，MVP 在第二轮优化中引入了安全工具并行执行机制（`SAFE_PARALLEL_TOOLS`），允许只读工具如 `read_file`、`web_search` 等通过 `asyncio.gather` 并行执行——这是一个**超越 Max 原始设计**的优化点，因为 Max 严格遵循单步执行原则。然而，这种并行化可能在某些场景下引入状态不一致的风险。

| 特性 | MVP 项目 | Manus 1.6 Max |
| --- | --- | --- |
| 循环步骤 | 7 步（含计划推进） | 6 步（事件驱动） |
| 最大迭代次数 | 25（可配置） | 无硬性上限（任务驱动） |
| 单步执行约束 | 安全工具可并行 | 严格单步执行 |
| 事件流驱动 | 部分实现（SSE 推送） | 完整事件流架构 |
| 循环检测 | 签名匹配 + 同工具名重复检测 | 未公开，推测有更复杂机制 |

### 3.2 模型路由与多模型支持

**Manus 1.6 Max** 的核心竞争力之一在于其**多模型路由架构**。根据公开信息，Max 至少使用了 Claude 3.7 Sonnet（复杂推理）、Qwen 系列（特定任务微调）、以及可能的 GPT-4.1 等模型。不同类型的任务（规划、执行、摘要、代码生成）会被路由到最合适的模型，实现成本与质量的最优平衡。

**MVP 项目** 在 `agent/model_router.py` 中实现了一个**模型路由框架**，定义了五种任务类型（planning、execution、summarization、sub_agent、code_generation），并支持通过环境变量配置不同任务使用不同模型。然而，在实际运行中，MVP 默认仅使用 DeepSeek Chat 单一模型，路由逻辑虽然存在但未被充分利用。MVP 同时兼容 Claude API（通过 `CLAUDE_API_KEY` 和 `CLAUDE_BASE_URL` 环境变量），这为接入更强模型提供了便利，但缺乏 Max 那种根据任务复杂度自动选择模型的智能决策能力。

---

## 四、上下文工程对比

上下文工程是 Manus 创始人 Peak Ji 在官方博客中着重强调的核心技术。这一领域的差异是 MVP 与 Max 之间最深层的技术鸿沟所在。

### 4.1 KV-Cache 优化

**Manus 1.6 Max** 将 KV-Cache 命中率视为"生产级 AI Agent 最重要的单一指标"。其实现包括三个关键实践：保持 prompt 前缀绝对稳定（不在开头插入时间戳等动态内容）、上下文严格 append-only（不修改历史记录，确保序列化确定性）、以及显式标记缓存断点。Max 的平均输入输出 token 比为 100:1，这意味着 KV-Cache 的优化对成本的影响极为显著——以 Claude Sonnet 为例，缓存命中与未命中的成本差距达 10 倍。

**MVP 项目** 在 `llm/system_prompt.py` 中声明了"KV-cache 友好"的设计原则，并在 `context_manager.py` 中实现了"只在尾部追加/裁剪"的压缩策略。然而，MVP 的 `build_system_prompt()` 函数中包含 `datetime.now().strftime("%Y-%m-%d %H:%M")` 动态时间戳注入，这**直接违反了 Max 的第一条 KV-Cache 优化原则**——虽然精度仅到分钟级别（而非秒级），但仍会导致每分钟的缓存失效。此外，MVP 缺乏显式的缓存断点标记机制。

### 4.2 工具可用性控制（Mask vs Remove）

**Manus 1.6 Max** 采用了一种精妙的**上下文感知状态机**来管理工具可用性。其核心原则是"Mask, Don't Remove"——不从上下文中动态增删工具定义（这会破坏 KV-Cache），而是通过在解码阶段对不相关工具的 token logits 进行掩码来控制动作空间。Max 还刻意将工具名称设计为具有一致前缀（`browser_*`、`shell_*`），以便通过 response prefill 技术约束模型只能选择特定工具组。

**MVP 项目** 在 `context_manager.py` 中实现了一个简化版的**动态工具门控**机制，根据当前计划阶段的 `capabilities` 字段筛选可用工具。但这种实现是通过**实际移除工具定义**来实现的（在 `build_messages` 中过滤 `TOOLS` 列表），而非 Max 的 logits masking 方式。这意味着每次工具集变化都会导致 KV-Cache 完全失效，与 Max 的设计理念背道而驰。

### 4.3 文件系统作为外部记忆

**Manus 1.6 Max** 将文件系统视为"终极上下文"——无限大小、持久化、可由 Agent 直接操作的外部记忆。其压缩策略始终是可恢复的：网页内容可以从上下文中移除，只要保留 URL；文档内容可以省略，只要保留文件路径。

**MVP 项目** 在 `context_manager.py` 中实现了类似的**文件外部化**策略，当工具结果超过阈值（默认 1000 字符，可通过 `MANUS_CONTEXT_EXTERNALIZE_THRESHOLD` 配置）时，自动将内容写入文件，上下文中只保留文件路径引用。这是 MVP 对 Max 设计理念的较好复刻。

### 4.4 todo.md 注意力操纵

**Manus 1.6 Max** 通过不断重写 `todo.md` 文件，将全局计划推到上下文的最末端，强制模型将注意力集中在核心目标上，对抗 Transformer 的"中间遗忘"（lost-in-the-middle）问题。根据泄露的提示词，Max 有明确的 `<todo_rules>` 指令要求基于 Planner 模块创建 todo.md，并在完成每个项目后立即更新标记。

**MVP 项目** 在 `context_manager.py` 中实现了**计划注入**机制，将当前计划的 Markdown 表示追加到上下文末尾。但 MVP 并未实现真正的 `todo.md` 文件机制——它只是将计划状态作为系统消息注入，而非让 Agent 主动读写文件。这种差异意味着 MVP 的注意力操纵效果弱于 Max。

### 4.5 错误记忆保留

两者在这一点上的设计理念高度一致。**Manus 1.6 Max** 明确指出"保留错误记录在上下文中"是提升 Agent 行为的最有效方式之一。**MVP 项目** 在 `context_manager.py` 中实现了**错误记忆保留窗口**（默认保留最近 10 条消息内的错误，可通过 `MANUS_ERROR_RETENTION_WINDOW` 配置），失败的工具调用在压缩时享有更高优先级。这是 MVP 对 Max 设计理念的忠实复刻。

| 上下文工程特性 | MVP 实现状态 | 与 Max 的差距 |
| --- | --- | --- |
| KV-Cache 友好设计 | 部分实现（有时间戳泄漏） | 中等 |
| Mask, Don't Remove | 未实现（使用 Remove 策略） | 较大 |
| 文件系统外部记忆 | 已实现 | 较小 |
| todo.md 注意力操纵 | *简化实现（计划注*入） | 中等 |
| 错误记忆保留 | 已实现 | 较小 |
| 可恢复压缩 | 已实现（`<compacted_history>` 标记） | 较小 |
| Few-Shot 陷阱防护 | 未实现 | 较大 |
| Token 精确计数 | 已实现（tiktoken） | 较小 |

---

## 五、规划系统对比

### 5.1 计划生成与修订

**Manus 1.6 Max** 的规划系统使用"编号伪代码"表示执行步骤，支持 `update`（创建/修订计划）和 `advance`（推进阶段）两种操作。每个阶段可标注所需能力（capabilities），计划会随着执行过程中发现的新信息动态修订。Max 的 Planner 是一个独立模块，其输出作为事件注入到事件流中。

**MVP 项目** 在 `agent/planner.py` 中实现了一个功能较为完整的规划系统，支持 LLM 驱动的计划生成（`create_plan_with_llm`）和模板回退（`create_template_plan`）。MVP 的 Planner 支持动态计划修订（`revise_plan`），每个 Phase 可标注 capabilities，并实现了复杂度自适应（简单 2 个阶段，复杂 8+ 个）。在第二轮优化中，MVP 还引入了基于迭代次数的智能阶段推进策略，解决了原有的"阶段卡死"问题。

总体而言，MVP 的规划系统在功能上与 Max 较为接近，是复刻程度最高的模块之一。但 Max 的 Planner 作为独立模块运行，可能使用专门的推理模型（如 DeepSeek Reasoner），而 MVP 的 Planner 使用与执行相同的模型。

### 5.2 Reasoning Effort 动态调整

**MVP 项目** 在 `context_manager.py` 中实现了一个有趣的特性——根据任务复杂度动态注入思考深度提示（`_inject_reasoning_effort`）。这种设计在 Max 的公开信息中未被明确提及，可能是 MVP 的**独创优化**，也可能是对 Max 内部机制的推测性实现。

---

## 六、工具体系对比

### 6.1 工具数量与覆盖范围

**Manus 1.6 Max** 拥有一个庞大而精细的工具体系。根据泄露的系统提示词和公开文档，Max 的工具至少包括：`message`（通信）、`plan`（计划管理）、`shell`（终端操作，支持多会话）、`file`（文件操作，支持 view/read/write/append/edit）、`match`（文件搜索，支持 glob/grep）、`search`（多类型搜索：info/image/api/news/tool/data/research）、`map`（并行子任务，最多 2000 个）、`browser_*`（完整浏览器自动化套件）、`generate`（多模态生成）、`slides`（PPT 生成）、`webdev_init_project`（Web 应用脚手架）、`schedule`（定时任务）、`expose`（端口暴露）等。

**MVP 项目** 注册了 **19 个工具**，覆盖了核心场景但远不及 Max 的完整性：

| 工具类别 | MVP 项目 | Manus 1.6 Max | 差距 |
| --- | --- | --- | --- |
| **搜索** | web_search, wide_research | search（7 种类型）, map（2000 并行） | 较大 |
| **终端** | shell_exec, execute_code | shell（多会话，view/exec/wait/send/kill） | 中等 |
| **文件** | read_file, write_file, edit_file, append_file, list_files, find_files, grep_files | file（view/read/write/append/edit）, match（glob/grep） | 较小 |
| **浏览器** | browser_navigate, browser_get_content, browser_screenshot, browser_click, browser_input, browser_scroll | browser_navigate, browser_view, browser_click, browser_input, browser_scroll, browser_move_mouse, browser_press_key, browser_select_option, browser_save_image, browser_upload_file, browser_find_keyword, browser_fill_form, browser_console_exec, browser_console_view, browser_close | 很大 |
| **多智能体** | spawn_sub_agents | map（最多 2000 子任务） | 较大 |
| **生成** | 无 | generate（图片/视频/音频/语音） | 缺失 |
| **演示文稿** | 无 | slides（HTML/Image 两种模式） | 缺失 |
| **Web 开发** | 无 | webdev_init_project（3 种脚手架） | 缺失 |
| **定时任务** | 无 | schedule（cron/interval） | 缺失 |
| **数据分析** | data_analysis | 通过 execute_code 实现 | MVP 有专门工具 |

### 6.2 浏览器自动化深度

这是两者差距最大的领域之一。**Max** 使用 Playwright 实现了完整的浏览器自动化，包括元素索引标注（返回 `index[:]<tag>text</tag>` 格式的可交互元素列表）、视口截图标注（用编号框标记交互元素）、表单批量填写、下拉菜单选择、键盘模拟、文件上传、JavaScript 控制台执行等。**MVP** 使用 Selenium 实现了基础的导航、点击、输入、滚动和截图功能，但缺乏元素索引、表单批量操作、键盘模拟等高级能力。

### 6.3 搜索能力

**Max** 的 `search` 工具支持 7 种搜索类型（info、image、api、news、tool、data、research），每次搜索可包含最多 3 个查询变体（query expansion），并支持时间过滤。**MVP** 仅支持基础的文本搜索（Tavily API 或 DuckDuckGo 回退），缺乏图片搜索、API 搜索、学术搜索等高级能力。

---

## 七、多智能体协作对比

### 7.1 并行处理架构

**Manus 1.6 Max** 的 `map` 工具支持最多 **2000 个并行子任务**，每个子任务在独立沙箱中运行，拥有完全隔离的文件系统和状态。子任务共享统一的输出 schema，支持 `file` 类型字段用于跨沙箱文件传递。

**MVP 项目** 在 `agent/parallel_enhanced.py` 中实现了增强版并行执行器，默认最大并发数为 5（可配置至 20），最大条目数为 20（硬限制 100）。MVP 的子代理共享同一进程空间，通过 `ContextVar` 实现会话隔离，而非 Max 的物理沙箱隔离。MVP 在第一轮优化中引入了 ETA 估算、输出模式验证、优雅降级等特性，这些在功能层面与 Max 较为接近。

### 7.2 子代理能力

**Max** 的子任务继承主代理的全部工具能力。**MVP** 的子代理仅允许使用 5 个工具（`web_search`、`read_file`、`write_file`、`browser_navigate`、`browser_get_content`），且最大迭代次数为 4（硬限制 12）。这种限制虽然降低了成本和风险，但也显著限制了子代理的任务处理能力。

| 并行处理特性 | MVP 项目 | Manus 1.6 Max |
| --- | --- | --- |
| 最大并行子任务 | 100（硬限制） | 2,000 |
| 隔离级别 | 进程级（ContextVar） | 沙箱级（独立 VM） |
| 子代理工具数 | 5 个 | 全部工具 |
| 子代理最大迭代 | 12（硬限制） | 无明确限制 |
| 自动重试 | 支持（指数退避） | 支持 |
| ETA 估算 | 支持 | 未公开 |
| 输出 Schema 验证 | 支持 | 支持 |

---

## 八、沙箱环境对比

**Manus 1.6 Max** 为每个任务分配一个完全隔离的云端虚拟机（Ubuntu 22.04），遵循"零信任"原则。沙箱具有持久化文件系统、完整的网络访问、预装的开发工具链，并支持休眠和唤醒。沙箱内的操作（包括 `sudo`）不会影响其他任务或核心服务。

**MVP 项目** 使用进程级隔离，每个会话有独立的工作目录（`/tmp/manus_workspace/{conversation_id}/`），通过 `_resolve_workspace_path` 函数阻止路径穿越。Shell 命令在子进程中执行，环境变量经过安全过滤（`SAFE_ENV_KEYS` 白名单）。这种隔离级别远低于 Max 的 VM 级隔离，存在潜在的安全风险——恶意代码可能影响宿主系统。

---

## 九、System Prompt 对比

### 9.1 结构化程度

**Manus 1.6 Max** 使用高度结构化的 XML 标签组织系统提示词，包括 `<intro>`、`<language_settings>`、`<system_capability>`、`<event_stream>`、`<agent_loop>`、`<planner_module>`、`<knowledge_module>`、`<datasource_module>`、`<todo_rules>`、`<message_rules>` 等分区。每个分区职责明确，便于模型理解和遵循。

**MVP 项目** 在 `llm/system_prompt.py` 中采用了类似的 XML 风格分区（`<agent_loop>`、`<tool_use>`、`<error_handling>`、`<file_operations>`、`<context_management>`、`<tool_selection>`、`<output_format>`），但缺少 Max 的 `<event_stream>`、`<knowledge_module>`、`<datasource_module>`、`<todo_rules>`、`<message_rules>` 等关键分区。

### 9.2 关键差异

| 提示词特性 | MVP 项目 | Manus 1.6 Max |
| --- | --- | --- |
| 结构化标签 | 7 个分区 | 12+ 个分区 |
| 事件流定义 | 无 | 完整的 7 种事件类型 |
| Knowledge 模块 | 无 | 任务相关知识动态注入 |
| Datasource 模块 | 无 | 数据 API 文档动态注入 |
| todo.md 规则 | 无专门规则 | 详细的创建/更新/验证规则 |
| Message 规则 | 简化 | 详细的 notify/ask 分类规则 |
| 动态时间戳 | 有（分钟级） | 无（KV-Cache 友好） |
| Skills 系统 | 无 | 渐进式披露的技能加载 |

---

## 十、前端体验对比

**MVP 项目** 的前端提供了一个功能性的聊天界面，包括消息气泡、工具调用卡片、计算机预览面板（终端、编辑器、浏览器三个标签页）、以及 SSE 实时流式输出。在两轮优化中，前端引入了代码语法高亮（`react-syntax-highlighter`）、增强的 Markdown 渲染、工具图标映射扩展、文件树防抖机制、以及本地化静态资源等改进。

**Manus 1.6 Max** 的前端体验显著更为丰富，包括 Design View（交互式图像编辑画布）、Slides 模式（PPT 生成和预览）、完整的浏览器预览（带元素标注的实时截图）、文件管理器（支持上传/下载）、以及多种导出格式（PDF、PPT、ZIP）。Max 的前端还支持移动端应用预览（通过 Expo 二维码扫描）。

---

## 十一、MVP 项目已实施的优化点总结

基于对项目代码和两份优化报告（`OPTIMIZATION_REPORT.md` 和 `OPTIMIZATION_ROUND2_REPORT.md`）的分析，MVP 项目已实施的优化可分为以下几个层次：

### 11.1 高价值优化（显著缩小与 Max 的差距）

1. **增强版 System Prompt**：从简单列表式升级为 XML 结构化分区，引入 Agent Loop 七步法、三次失败规则、文件操作检查点策略等精细指令。

1. **KV-Cache 友好的上下文压缩**：实现了 append-only 压缩策略、`<compacted_history>` 标记、错误记忆保留窗口，以及文件外部化机制。

1. **动态计划修订系统**：支持 LLM 驱动的计划生成和修订、阶段能力标注、复杂度自适应，以及基于迭代次数的智能阶段推进。

1. **Token 精确计数**：引入 `tiktoken` 库实现精确的 Token 预算管理，替代原有的粗略字符计数。

1. **并行工具执行**：安全工具白名单机制，允许只读工具并行执行，提升效率。

### 11.2 中等价值优化（改善用户体验和稳定性）

1. **文件搜索工具扩展**：新增 `find_files`（glob 模式）和 `grep_files`（正则搜索），对标 Max 的 `match` 工具。

1. **增强循环检测**：在完全签名匹配基础上增加同工具名重复检测，防止 Agent 陷入死循环。

1. **代码语法高亮**：引入 `react-syntax-highlighter`，提供 VS Code Dark+ 主题的代码渲染。

1. **文件树防抖**：解决前端高频轮询 `/api/sandbox/files` 的性能问题。

1. **本地化静态资源**：将外部 CDN 图片替换为 SVG 内联图标，消除外部依赖。

### 11.3 框架性优化（搭建了扩展基础但尚未充分利用）

1. **模型路由框架**：定义了五种任务类型的路由规则，但默认仅使用单一模型。

1. **多智能体增强**：ETA 估算、输出模式验证、优雅降级等特性，但并发规模远小于 Max。

---

## 十二、仍存在的核心差距与建议优化方向

### 12.1 架构级差距（需要重大重构）

| 差距领域 | 当前状态 | 建议方向 | 优先级 |
| --- | --- | --- | --- |
| 沙箱隔离 | 进程级 | 引入 Docker 容器化，每会话独立容器 | P0 |
| 浏览器自动化 | Selenium 基础功能 | 迁移到 Playwright，实现元素索引和截图标注 | P0 |
| 多模型路由 | 框架存在但未启用 | 接入 Claude/GPT-4.1，实现智能路由 | P1 |
| 工具 Logits Masking | 使用 Remove 策略 | 实现 response prefill 约束 | P1 |

### 12.2 功能级差距（需要新增模块）

| 缺失功能 | Max 的实现 | 建议方向 | 优先级 |
| --- | --- | --- | --- |
| 多模态生成 | generate 工具 | 集成 DALL-E/Stable Diffusion API | P1 |
| PPT/Slides 生成 | slides 模式（HTML/Image） | 实现 HTML 模式的 Slides 生成 | P2 |
| Web 应用脚手架 | webdev_init_project | 集成 Vite/Expo 项目模板 | P2 |
| 定时任务 | schedule 工具（cron/interval） | 实现基于 APScheduler 的任务调度 | P2 |
| Skills 系统 | 渐进式披露的技能加载 | 实现 SKILL.md 格式的技能包 | P2 |
| Knowledge 模块 | 任务相关知识动态注入 | 实现基于向量检索的知识库 | P3 |
| Datasource 模块 | 数据 API 文档动态注入 | 实现 API 注册和文档管理 | P3 |

### 12.3 细节级差距（可快速修复）

| 问题 | 当前状态 | 修复建议 | 优先级 |
| --- | --- | --- | --- |
| System Prompt 时间戳 | 分钟级动态注入 | 移至上下文末尾或移除 | P0 |
| todo.md 机制 | 仅计划注入 | 实现真正的 todo.md 文件读写 | P1 |
| Few-Shot 陷阱防护 | 未实现 | 引入序列化模板变异 | P1 |
| Shell 多会话 | 单会话 | 支持命名会话和会话切换 | P2 |
| 消息类型分类 | 简化 | 实现 notify/ask/result 三种类型 | P2 |

---

## 十三、结论

wmc-cache/manus 项目作为一个开源 MVP，在有限的资源下实现了对 Manus 1.6 Max 核心架构理念的较好复刻。项目在**规划系统**、**错误记忆保留**、**文件外部化**、**Token 精确计数**等方面与 Max 的差距较小；在**上下文工程**（KV-Cache 优化、Mask vs Remove）、**工具体系完整性**、**浏览器自动化深度**、**多智能体并行规模**等方面仍存在显著差距；而在**沙箱隔离级别**、**多模型智能路由**、**多模态生成**、**Skills 系统**等方面则存在架构级的缺失。

值得注意的是，MVP 项目在某些方面展现了独到的优化思路，如安全工具并行执行、Reasoning Effort 动态调整、以及基于迭代次数的智能阶段推进等，这些特性在 Max 的公开信息中未被明确提及，可能代表了有价值的探索方向。

总体评估，MVP 项目大约实现了 Manus 1.6 Max **30-40%** 的核心能力，在学习和理解 AI Agent 架构设计方面具有很高的参考价值，但距离生产级应用仍有较大差距。最关键的优化方向是沙箱容器化、浏览器自动化升级、以及多模型路由的实际启用。

