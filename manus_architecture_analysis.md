# Manus 多智能体架构深度解析

**作者**: Manus AI
**日期**: 2026年2月21日

## 摘要

Manus 作为一个先进的通用人工智能（AGI）代理，其强大的任务执行能力根植于一个复杂而精妙的多智能体架构。本文深入剖析了 Manus 系统的核心架构设计，详细阐述了其关键组件、工作流程、以及包括 CodeAct、多智能体协作、上下文工程和沙箱环境在内的核心机制。通过结合伪代码示例，本报告旨在为理解和研究高级 AI Agent 系统提供一个清晰的技术蓝图。

## 1. 宏观架构：一个分布式的认知与执行系统

从根本上说，Manus 并非一个单一的、巨大的模型，而是一个构建在多个业界领先的基础模型（如 Anthropic 的 Claude 系列和阿里巴巴的 Qwen 系列）之上的**分布式智能体编排系统** [2]。它的设计哲学是将“思考”（认知与规划）和“行动”（工具使用与代码执行）分离，并通过一个高度结构化的方式将它们结合起来。其架构的核心特点是模块化、可扩展和高容错性。

系统的顶层设计可以概括为几个协同工作的核心模块，它们共同构成了一个完整的自主代理（Autonomous Agent）。下图展示了 Manus 多智能体架构的全景视图：

![Manus 多智能体架构全景图](https://private-us-east-1.manuscdn.com/sessionFile/nhnqL1CwycNNLMk0kykGyZ/sandbox/QfyZ4TdWJRAs7I1usFnBQT-images_1771683090723_na1fn_L2hvbWUvdWJ1bnR1L21hbnVzX2FyY2g.png?Policy=eyJTdGF0ZW1lbnQiOlt7IlJlc291cmNlIjoiaHR0cHM6Ly9wcml2YXRlLXVzLWVhc3QtMS5tYW51c2Nkbi5jb20vc2Vzc2lvbkZpbGUvbmhucUwxQ3d5Y05OTE1rMGt5a0d5Wi9zYW5kYm94L1FmeVo0VGRXSlJBczdJMXVzRm5CUVQtaW1hZ2VzXzE3NzE2ODMwOTA3MjNfbmExZm5fTDJodmJXVXZkV0oxYm5SMUwyMWhiblZ6WDJGeVkyZy5wbmciLCJDb25kaXRpb24iOnsiRGF0ZUxlc3NUaGFuIjp7IkFXUzpFcG9jaFRpbWUiOjE3OTg3NjE2MDB9fX1dfQ__&Key-Pair-Id=K2HSFNDJXOU9YS&Signature=AzMieASKZqEVJQksJgEP1XjgmclcaTJ9uf~lBuVvEUimkmvaCwJEqL1lbo6aS4~s~rw87w1w8szaSs2vP0oNRZrX3hbAcCOn1O8K7KZoT9ITpfPOYHC9sOfsdDss9cbBatmUQzhzPXkE9aCd2owoYnXodl-aPBg738QA5oHntGfnRYeYeZI7KdNW50~VxZkIAudKMt6JL9WzhjgjKtYe9hROZ6eMu8nuWff8qtOxvp-QvwuonXNJ5u4cquhJ4ci1MoMewR9DRcF01Uwl9p6nh1fB4xGrbLCu9FWU~WqNs8s9u3oxbbrMTWsvLp1DaWmNvWBb3S927vfKJSoLeAMxSQ__)

| 核心模块 | 主要功能 |
| --- | --- |
| **编排器 (Orchestrator)** | 作为系统的“大脑”，负责接收用户请求，协调其他所有模块，并驱动 Agent Loop。 |
| **规划器 (Planner)** | 将复杂、高层次的用户目标分解为一系列具体的、可执行的步骤。 |
| **执行引擎 (Execution Engine)** | 负责执行规划器产生的步骤，核心是 CodeAct 范式，通过生成和执行代码与环境交互。 |
| **沙箱环境 (Sandbox)** | 为每个任务提供一个完全隔离、安全的云端虚拟计算环境（Ubuntu VM），包含文件系统、网络、浏览器等。 |
| **记忆与上下文模块 (Memory & Context)** | 管理代理的短期工作记忆（事件流）、长期知识（文件系统、技能库）和上下文窗口。 |
| **多智能体协作模块 (Multi-Agent Collaboration)** | 支持两种协作模式：功能专业化的子代理协作，以及用于大规模并行处理的“Wide Research”模式。 |
| **技能模块 (Skills)** | 允许系统通过加载标准化的“技能”文件来动态扩展其在特定领域的专业能力。 |

这种模块化的设计使得 Manus 能够灵活地调用不同模型的优势（例如，使用 Claude 3.7 进行复杂逻辑推理，使用微调的 Qwen 模型执行特定任务），并能独立地对每个组件进行升级和优化，而不会影响整个系统的稳定性 [2]。

## 2. Agent Loop：核心工作流程

Manus 的所有操作都围绕一个迭代式的**“分析 → 规划 → 执行 → 观察” (Analyze → Plan → Execute → Observe) 循环**进行，这通常被称为 Agent Loop [2]。这个循环确保了代理的每一步行动都是基于当前状态和历史信息的深思熟虑的结果。

> “Manus operates through an iterative agent loop that structures its autonomy. At a high level, each cycle of the loop consists of: (1) Analyzing the current state and user request, (2) Planning/Selecting an action, (3) Executing that action in the sandbox, and (4) Observing the result, which gets appended to the event stream.” [2]

这个流程可以通过以下伪代码来清晰地表述：

```python
def agent_loop(user_request):
    # 初始化上下文和记忆
    context = initialize_context(user_request)
    memory = initialize_memory()

    # 规划器分解任务
    plan = context.planner.create_plan(user_request)
    memory.add_to_long_term("todo.md", plan.to_markdown())

    while not plan.is_complete():
        # 1. 分析 (Analyze)
        # 模型基于当前上下文和记忆，决定下一步行动
        current_state = {
            "plan": plan.get_status(),
            "recent_history": context.get_recent_events(),
            "relevant_knowledge": memory.retrieve_relevant_knowledge(plan.get_current_step())
        }

        # 2. 规划/选择行动 (Plan/Select Action)
        # 核心是 CodeAct，生成 Python 代码作为行动
        action_code = context.llm.decide_next_action(current_state)

        # 3. 执行 (Execute)
        # 在隔离的沙箱中执行生成的代码
        observation = context.sandbox.execute(action_code)

        # 4. 观察 (Observe)
        # 将行动和观察结果记录到上下文中
        context.append_event({"action": action_code, "observation": observation})

        # 如果出现错误，记录下来供模型学习
        if observation.is_error():
            memory.add_to_long_term("error_log.txt", str(observation))

        # 更新计划状态
        plan.update(observation)
        memory.update_long_term("todo.md", plan.to_markdown())

    # 任务完成，生成最终报告
    final_report = context.llm.generate_final_output(context.get_full_history())
    return final_report

```

值得注意的是，Manus 在每次循环中严格限制只执行一个工具动作，并等待其返回结果 [2]。这种单步执行的控制流，结合对 `todo.md` 文件的持续引用和更新，极大地增强了任务执行的稳定性和可追溯性，有效避免了在平均长达50次工具调用的复杂任务中出现“目标漂移”的问题 [1]。

## 3. 核心机制深度解析

Manus 的强大能力不仅仅来自于其宏观架构，更体现在其一系列精心设计的核心机制上。

### 3.1 CodeAct：将代码作为通用行动语言

传统 AI Agent 通常依赖于预定义的、基于 JSON 的工具调用（Tool Calling）。Manus 则采用了一种更灵活、更强大的范式——**CodeAct**，即直接生成和执行 Python 代码来与环境交互 [6]。这种方法的灵感来源于 2024 年的一篇研究论文，该论文指出，让大语言模型生成可执行代码作为动作，可以显著提升其在复杂任务上的成功率 [2]。

> “CodeAct equips AI agents with a Python Interpreter to write and execute Python code instead of making JSON function calls with built-in tool calling parameters, making them more efficient at solving complex problems.” [6]

**优势**: 
- **灵活性与组合性**: 代码可以轻松地组合多个工具、处理条件逻辑、进行数据转换，从而实现远超单个工具调用的复杂工作流。
- **强大的生态系统**: 代理可以直接利用 Python 庞大的第三方库生态（如 Pandas, Matplotlib, Requests）来完成数据分析、可视化、API 调用等高级任务。
- **自调试与错误恢复**: 当代码执行出错时，Python 解释器会返回详细的错误信息（Stack Trace）。Manus 将这个错误信息作为观察结果反馈给大模型，模型可以据此“理解”错误原因，并尝试修改代码进行下一轮尝试，实现了一种有效的自调试循环 [1]。

**伪代码示例：结合工具调用**

```python
# LLM 生成的 CodeAct 代码，用于搜索并总结信息

def research_and_summarize(topic):
    try:
        # 调用内置的浏览器工具函数
        search_results = browser.search(query=f"latest research on {topic}")

        # 循环处理搜索结果
        summary = ""
        for result in search_results[:3]: # 只看前三个结果
            page_content = browser.navigate(url=result.url)
            # 调用另一个 LLM 实例进行总结
            summary += llm.summarize(page_content)

        # 将结果写入文件
        file.write("research_summary.txt", summary)
        return "Successfully researched and saved summary."
    except Exception as e:
        # 返回错误信息，供上层 Agent 分析
        return f"An error occurred: {str(e)}"

# 执行代码
research_and_summarize("AI agent architectures")
```

### 3.2 多智能体协作：分而治之与并行处理

Manus 架构的另一大亮点是其原生的多智能体协作能力。它并非一个“独行侠”，而是可以根据任务需要，动态地“雇佣”多个专业子代理协同工作，或者将大规模任务分解后并行处理 [2]。

#### 3.2.1 功能专业化的子代理协作

对于需要多种不同技能的复杂项目（如开发一个网站），主编排器会将任务分解，并分配给具有不同专业能力的子代理。例如：
- **研究代理 (Research Agent)**: 负责市场调研、竞品分析。
- **开发代理 (Developer Agent)**: 负责编写前端和后端代码。
- **测试代理 (QA Agent)**: 负责测试网站功能，并报告 Bug。

这些代理在各自独立的沙箱环境中工作，通过一个共享的记忆系统（如文件系统或消息总线）进行通信和状态同步。主编排器则负责监控全局进度，并在所有子任务完成后整合最终结果。

**伪代码示例：多智能体协作编排**

```python
class Orchestrator:
    """主编排器：协调多个专业子代理完成复杂项目"""

    def __init__(self, llm, sandbox_pool):
        self.llm = llm
        self.sandbox_pool = sandbox_pool
        self.shared_memory = SharedFileSystem()

    def execute_project(self, user_request):
        # 1. 任务分解：将项目拆分为子任务并分配角色
        subtasks = self.llm.decompose_task(user_request)
        # 例如: [{"role": "researcher", "task": "调研竞品"},
        #        {"role": "developer", "task": "开发前端"},
        #        {"role": "qa",        "task": "测试功能"}]

        # 2. 为每个子代理分配独立沙箱
        agents = []
        for subtask in subtasks:
            sandbox = self.sandbox_pool.allocate()
            agent = SpecializedAgent(
                role=subtask["role"],
                llm=self.llm,
                sandbox=sandbox,
                shared_memory=self.shared_memory
            )
            agents.append((agent, subtask))

        # 3. 按依赖关系调度执行（部分可并行）
        dependency_graph = self.llm.build_dependency_graph(subtasks)
        for stage in dependency_graph.topological_order():
            parallel_tasks = [a for a in agents if a[1] in stage]
            results = run_in_parallel(
                [(agent.execute, task) for agent, task in parallel_tasks]
            )
            # 将每个阶段的结果写入共享记忆
            for result in results:
                self.shared_memory.write(result.output_path, result.data)

        # 4. 整合所有子代理的产出
        final_output = self.llm.synthesize(
            self.shared_memory.read_all_outputs()
        )
        return final_output


class SpecializedAgent:
    """专业子代理：在独立沙箱中执行特定角色的任务"""

    def execute(self, subtask):
        # 加载角色相关的技能和知识
        skill = self.load_skill(subtask["role"])
        context = self.build_context(subtask, skill)

        # 在自己的沙箱中运行标准 Agent Loop
        return agent_loop(context)
```

#### 3.2.2 Wide Research：大规模并行处理

当面临需要对大量同类项目进行重复操作的任务时（例如，“分析100家公司的财报”或“为50个产品生成描述”），Manus 会启动其 **Wide Research** 功能。这本质上是一个**MapReduce**模式的实现 [8]。

> “Instead of using a single AI agent that processes items sequentially, Wide Research deploys hundreds of independent agents that work in parallel. Each agent receives its own dedicated context and processes one item independently.” [8]

**工作流程**:
1.  **Map (映射/分解)**: 主代理将大任务分解成一系列独立的子任务（例如，将“分析100家公司”分解为100个“分析公司X”的子任务）。
2.  **Parallel Execution (并行执行)**: 系统为每个子任务启动一个全新的、独立的、临时的 AI 代理。每个代理都在自己的沙箱中工作，拥有干净的上下文窗口，因此不会受到其他代理的影响。
3.  **Reduce (规约/综合)**: 主代理收集所有子代理的执行结果，然后进行汇总、分析，并整合成最终的交付物（如一份完整的分析报告或一个数据表格）。

这种架构从根本上解决了传统单代理系统中，随着处理项目增多导致上下文窗口爆炸和性能下降的问题，确保了对每一个项目的处理质量都同样深入。

**伪代码示例：Wide Research (MapReduce 模式)**

```python
def wide_research(user_request, items_to_process):
    """
    Wide Research 的核心实现：
    将大规模同质任务分发给数百个独立代理并行处理。
    """
    # ====== MAP 阶段 ======
    # 主代理分析请求，生成子任务模板
    task_template = main_agent.analyze_and_create_template(user_request)
    # 例如: "Research company {item} and extract: revenue, employees, products"

    subtasks = []
    for item in items_to_process:  # 可能有 100~2000 个项目
        subtasks.append({
            "prompt": task_template.format(item=item),
            "output_schema": task_template.output_schema,
            "sandbox": sandbox_pool.allocate_ephemeral()  # 临时沙箱
        })

    # ====== PARALLEL EXECUTION 阶段 ======
    # 每个子任务由一个独立的、全新的代理处理
    # 关键：每个代理拥有自己干净的上下文窗口，互不干扰
    results = parallel_map(
        func=lambda task: spawn_fresh_agent(task).execute(),
        inputs=subtasks,
        max_concurrency=200  # 最多 200 个代理同时工作
    )

    # ====== REDUCE 阶段 ======
    # 主代理收集所有结果并整合
    aggregated_data = main_agent.aggregate(results)
    final_deliverable = main_agent.format_output(
        aggregated_data,
        format=user_request.desired_format  # 表格、报告、数据集等
    )

    return final_deliverable
```

### 3.3 沙箱环境：安全、隔离的“世界模型”

Manus 的所有行动都发生在一个为每个任务专门创建的、完全隔离的**云端虚拟计算机**中，即 **Manus Sandbox** [4]。这个沙箱不仅仅是一个受限的执行环境，而是一个功能完备的 Ubuntu Linux 虚拟机，配备了网络、文件系统、浏览器和各种预装软件 [2] [4]。

> “Manus Sandbox is a fully isolated cloud virtual machine that Manus allocates for each task. Each Sandbox runs in its own environment, does not affect other tasks, and can execute in parallel.” [4]

这种设计的核心价值在于：
- **安全性与隔离性**: 遵循“零信任”原则，沙箱内的任何操作（即使是 `sudo` 或破坏性命令）都不会影响到其他任务或 Manus 的核心服务。这使得代理可以拥有极高的自由度来完成任务，而不必担心安全风险 [4]。
- **持久性与状态保持**: 沙箱的文件系统是持久化的。代理可以将代码、数据、中间结果、配置文件等保存在沙箱中。即使任务暂停（沙箱进入休眠状态），这些状态也会被完整保留，确保了复杂、长周期任务的连续性 [4]。
- **环境一致性**: 为每个任务提供一个标准化的、干净的环境，确保了任务执行的可复现性。
- **外部记忆的物理载体**: 沙箱的文件系统是 Manus 记忆和上下文管理策略的物理基础，它充当了代理的“外部硬盘”，可以无限扩展其记忆容量。

### 3.4 上下文工程：在有限窗口内实现无限记忆

对于需要执行数十甚至上百步操作的复杂任务，如何有效管理大语言模型的上下文窗口是一个核心挑战。Manus 采用了一套被称为“上下文工程” (Context Engineering) 的精细策略，其核心思想是：**将沙箱的文件系统作为无限的上下文存储，并仅在需要时将最相关的信息加载到模型的有限注意力窗口中** [1]。

**伪代码示例：上下文管理状态机**

```python
class ContextManager:
    """上下文工程的核心实现：管理有限窗口中的无限记忆"""

    def __init__(self, max_context_tokens=128000):
        self.max_tokens = max_context_tokens
        self.event_stream = []        # 短期工作记忆
        self.file_system = FileSystem()  # 长期外部记忆
        self.tool_state_machine = ToolStateMachine()

    def build_llm_input(self, plan):
        """构建发送给 LLM 的上下文，确保 KV-Cache 命中率最大化"""
        context_parts = []

        # 1. 系统提示词（保持稳定，不插入时间戳等动态内容）
        context_parts.append(self.system_prompt)  # 固定前缀

        # 2. 工具定义（始终完整保留，通过 logits masking 控制可用性）
        context_parts.append(self.all_tool_definitions)  # 不动态增删

        # 3. 事件流（仅追加，不修改历史记录）
        for event in self.event_stream:
            if self.estimate_tokens(context_parts) > self.max_tokens * 0.8:
                # 压缩策略：将旧的观察替换为文件路径引用
                event = self.compress_event(event)
            context_parts.append(event)

        # 4. 当前计划状态（todo.md 内容置于末尾，操纵注意力）
        context_parts.append(self.file_system.read("todo.md"))

        return context_parts

    def compress_event(self, event):
        """可恢复的压缩：保留路径/URL，丢弃原始内容"""
        if event.type == "web_page":
            return f"[Web page content saved at {event.url}, omitted for brevity]"
        elif event.type == "file_content":
            return f"[File content at {event.path}, read when needed]"
        return event  # 保留错误信息等关键事件

    def get_allowed_tools(self, current_state):
        """通过状态机决定当前允许的工具，用于 logits masking"""
        return self.tool_state_machine.get_allowed_actions(current_state)
        # 例如：用户刚发消息 → 只允许 message 工具
        #       当前在浏览网页 → 只允许 browser_* 工具
```

关键策略包括：
- **将文件系统作为外部记忆**: 代理被训练成习惯性地将任何重要的、冗长的信息（如网页内容、代码文件、研究笔记）写入沙箱的文件中，而在上下文中只保留该文件的路径或一个简短的摘要。当后续步骤需要这些信息时，代理会生成代码去读取相应的文件 [1]。
- **`todo.md` 作为注意力焦点**: 通过在每次迭代中读取和重写一个名为 `todo.md` 的任务清单文件，代理能够将当前的全局计划和进度“置顶”到上下文的最末端，从而强制模型将注意力集中在核心目标上，对抗自然语言模型在长上下文中的“中间遗忘”问题 [1]。
- **保留失败记录**: 与试图隐藏错误的普遍做法相反，Manus 会故意将失败的动作、返回的错误码和堆栈跟踪信息保留在上下文中。这为模型提供了宝贵的负反馈，使其能够从错误中学习，避免在后续步骤中重复同样的错误 [1]。
- **掩码而非移除 (Mask, Don't Remove)**: 为了在不同阶段约束代理的行为（例如，在某些步骤中只允许使用浏览器工具），Manus 并不从上下文中动态移除工具定义（这会破坏 KV-Cache），而是通过在解码阶段对不相关工具的 token logits 进行掩码（masking），来阻止模型选择它们。这既保证了上下文的稳定性，又实现了动态的动作空间控制 [1]。

### 3.5 技能 (Skills)：模块化的能力扩展

为了让代理能够处理高度专业化的任务，Manus 引入了 **Agent Skills** 机制。一个“技能”是一个封装了特定领域知识、最佳实践工作流和相关脚本的目录，其核心是一个 `SKILL.md` 文件 [5]。

> “Agent Skills are an innovative approach that packages expertise, workflows, and best practices into reusable, file-system-based resources. You can think of them as an 'onboarding guide for new employees.'” [5]

当代理识别出当前任务与某个已安装的技能相关时，它会加载该技能。技能系统采用“渐进式披露” (Progressive Disclosure) 的设计，`SKILL.md` 中的内容被分为不同层级，代理只会按需加载最相关的部分到上下文中，从而以极高的上下文效率获取专业知识和操作指南 [5]。这使得一个通用的 Manus 代理可以被快速“特化”为一个金融分析师、一个法律合同审查员或一个社交媒体营销专家。

## 4. 结论

Manus 的多智能体架构是一个将大型语言模型的认知能力与传统软件工程的稳健性、模块化和可扩展性相结合的典范。它通过以下几个关键设计，成功地将一个通用的“思考引擎”转化为一个能够真正在数字世界中可靠“行动”的执行者：

- **CodeAct 范式**赋予了代理前所未有的灵活性和与环境交互的深度。
- **多智能体协作机制**（包括功能分解和并行处理）使其能够高效地处理单一代理无法完成的复杂和大规模任务。
- **隔离的沙箱环境**在提供极高操作自由度的同时，保证了系统的安全与稳定。
- **精密的上下文工程**策略，特别是将文件系统作为外部记忆，巧妙地绕过了大模型上下文窗口的物理限制，为实现长期、复杂的任务执行铺平了道路。

总而言之，Manus 的架构设计展示了一条将 AI Agent 从“玩具”推向“生产力工具”的清晰路径。它并非依赖于某个单一的、突破性的模型，而是通过一系列务实而巧妙的工程实践，构建了一个能够协同、学习和成长的分布式智能系统。

## 5. 参考文献

[1] Manus Team. (2025, July 18). *Context Engineering for AI Agents: Lessons from Building Manus*. Manus Blog. Retrieved from https://manus.im/blog/Context-Engineering-for-AI-Agents-Lessons-from-Building-Manus

[2] renschni. (n.d.). *In-depth technical investigation into the Manus AI agent*. GitHub Gist. Retrieved from https://gist.github.com/renschni/4fbc70b31bad8dd57f3370239dccd58f

[3] Medium. (2025, March 31). *Inside Manus: The Anatomy of an Autonomous AI Agent*. Retrieved from https://medium.com/@jalajagr/inside-manus-the-anatomy-of-an-autonomous-ai-agent-b3042e5e5084

[4] Manus Team. (2026, January 14). *Understanding Manus sandbox - your cloud computer*. Manus Blog. Retrieved from https://manus.im/blog/manus-sandbox

[5] Manus Team. (2026, January 27). *Manus AI Embraces Open Standards: Integrating Agent Skills*. Manus Blog. Retrieved from https://manus.im/blog/manus-skills

[6] Saboo, S., & Gupta, G. (2025, March 28). *Architecture Behind Manus AI Agent*. unwind ai. Retrieved from https://www.theunwindai.com/p/architecture-behind-manus-ai-agent

[7] CSDN. (2025, March 11). *Manus 架构设计揭秘*. Retrieved from https://blog.csdn.net/Python_cocola/article/details/146185191

[8] Manus Team. (n.d.). *Wide Research*. Manus Documentation. Retrieved from https://manus.im/docs/features/wide-research
