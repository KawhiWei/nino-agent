# 第一章：别急着写智能体，先把执行边界立起来

> Nino Agent Harness 工程实践（一）
> 代码基线：`ee55b76`，2026-07-17
> 版本标记：`0.10.0`

很多 Agent 项目的第一版都很相似：准备一段 system prompt，把工具列表交给模型，然后写一个 `while` 循环，直到模型返回文本。这个方案很适合验证想法，但一旦进入真实业务，问题很快会从“模型会不会调用工具”变成另一组更难的问题：

- 一次任务最多允许模型走几步、调用几次工具？
- 模型为什么能看到这个工具，它有没有权限调用？
- 用户取消、服务重启或 MCP 超时时，任务处于什么状态？
- 前端怎样知道 Agent 正在规划、查询还是校验？
- 更换模型、MCP 服务或执行引擎时，业务接口是否要跟着变化？

这些问题不属于 Prompt Engineering，而属于执行系统工程。Nino Agent 的第一个完整提交没有把 Agent 理解为“模型加工具”，而是先建立了一组边界，把模型放进一个受控执行环境中。这个环境就是 Harness。

## 1. 先说明“最早版本”到底有多早

仓库最早有一个占位提交 `4fd8492`，但没有可讨论的系统设计。第一个完整项目提交是 `ee55b76`。它一次增加了 102 个文件、约 11165 行代码，已经包含：

- FastAPI REST/SSE Host；
- Conversation、Run、Event 与 SQLite 持久化；
- 轻量 ReAct 与 LangGraph 两种 Worker 实现；
- Agent、Skill、Reference 和 MCP 工具治理；
- Orchestrator 与 Specialist 两层循环；
- Demo/Live 两种运行模式；
- Python Agent 与 .NET MCP Server；
- 分层、API、持久化、MCP 和 Harness 测试。

所以这组文章不会虚构一个并不存在的“几十行聊天机器人阶段”。真正值得复盘的是：为什么 MVP 第一天就选择了这些边界，以及这些边界后来如何承接 TaskGraph、Planner、评测、流式 UI 和容器化。

## 2. Harness 不是框架的别名

在 Nino Agent 中，Harness 不是 LangGraph 的包装器，也不是某个模型 SDK 的适配层。它是模型与真实世界之间的执行治理层。

可以把最早版本的调用关系概括成：

```text
HTTP / SSE
    -> AgentRuntimeService
        -> AgentHarness Port
            -> OrchestratorHarness
                -> ReActHarness 或 LangGraphReActHarness
                    -> ChatModel Port
                    -> ToolProvider Port
                        -> MCP Server Registry
```

这条链路中，每一层回答不同的问题：

| 层 | 核心问题 | 不应该负责 |
|---|---|---|
| API | 请求如何进入，事件如何流出 | 推理策略、工具选择 |
| Runtime | Run 如何排队、取消、持久化和恢复上下文 | Prompt、业务路由、MCP 协议 |
| Harness | 模型能做什么、如何循环、何时停止、如何派发 | HTTP、SQLite、具体传输实现 |
| Framework | 各层依赖的稳定实体和 Port 是什么 | 外部 SDK 和业务规则 |
| Infrastructure | 模型、MCP、Repository 如何接入 | Agent 的规划与回答策略 |

这一区分非常关键。Runtime 管理“任务的生命”，Harness 管理“任务怎么执行”。如果把二者混在一起，HTTP 超时、数据库事务、Prompt 拼装和工具策略最终会堆进同一个服务类，任何一次模型或框架替换都会牵动整个系统。

对于 Nino Agent 这类企业服务端系统，还要再区分两类状态。控制面保存的是 Graph State：用户目标、节点、依赖、Gate、revision 和执行进度；Worker 处理的是 Work State：当前业务细节、Tool Result、局部假设和临时推理。Orchestrator 不应把所有 Worker 日志重新吞进主上下文，只消费结构化 Node Result。这样服务重启或请求跨会话继续时，Runtime 读取的是数据库里的任务事实，而不是让模型从聊天记录重新猜测。

Nino 的入口是 REST/SSE，Graph Truth 由服务端 Runtime/Repository 持久化。模型和 Worker 不能直接写数据库，只能通过受控 Action 和结构化 Event 表达执行结果；Runtime 负责把事件投影为可恢复的任务状态。

## 3. 从 Port 开始，而不是从 SDK 开始

最早版本在 `framework/ports.py` 中定义了三个很小的协议：

```python
class AgentHarness(Protocol):
    async def step(self, state: HarnessStepState) -> ModelTurn: ...
    async def run(self, user_input, history=(), on_event=None, run_id=None) -> RunResult: ...

class ChatModel(Protocol):
    async def complete(self, messages, tools) -> ModelTurn: ...

class ToolProvider(Protocol):
    async def list_tools(self) -> Sequence[ToolDefinition]: ...
    async def invoke(self, call: ToolCall) -> ToolResult: ...
```

这里有三个设计信号。

第一，Harness 对 Runtime 暴露的是稳定运行契约，而不是 LangGraph 的 Graph、LangChain 的 AgentExecutor 或某家模型的 Response 对象。外部框架只是实现细节。

第二，模型和工具被分别抽象。模型负责产生文本或结构化调用，ToolProvider 负责发现与执行工具。Harness 位于二者中间，先做权限、预算和重复调用校验，再决定真实动作能否发生。

第三，`step` 和 `run` 被明确区分。`step` 只表示一次模型决策；`run` 才是一段有边界的多步执行。这个区分后来为统一 Loop 状态、TaskGraph 节点执行和流式进度打下了基础。

Port 的价值不在于“面向接口编程”这句口号，而在于建立替换成本的上限。初版已经可以组合：

```text
native model + lightweight loop
LangChain model + lightweight loop
native model + LangGraph loop
LangChain model + LangGraph loop
```

API 和 Runtime 不需要知道实际选中了哪一组。

## 4. Runtime 管生命周期，Harness 管决策

`AgentRuntimeService` 接收一条用户消息后，会创建 Run、保存消息、限制并发、恢复历史上下文，然后把执行交给 `AgentHarness.run(...)`。Harness 通过回调持续产生事件，Runtime 负责按顺序持久化这些事件，最后写入回答或错误状态。

```text
submit_message
  -> 保存 user message 和 queued run
  -> 后台任务取得并发槽位
  -> 恢复 conversation context
  -> harness.run(on_event=save_event)
  -> 持久化有序事件
  -> 写入 terminal run 和 assistant message
```

这意味着 Harness 不需要知道 SQLite 表结构，Runtime 也不需要知道模型为什么选择某个工具。二者通过 `RunResult` 和 `AgentEvent` 交流。

这个边界带来一个很实用的结果：SSE 不是模型 Token 流的简单转发，而是 Run 的可重放执行记录。客户端断线重连后可以按事件序号继续读取；测试也可以检查是否真实出现了 `tool_started`、`tool_completed` 和 `loop_checkpoint`，而不是只断言最终回答里有几个关键词。

## 5. 两层循环：控制面不碰业务工具

最早版本已经把执行分成两层：

```text
Orchestration Loop
  理解请求 -> 直接回答，或选择 Agent + Skill -> 汇总结果 -> 结束/继续派发

Specialist Worker Loop
  加载 Skill -> Reason -> Action -> Observation -> 回答
```

Orchestrator 是控制面。它只看到动态能力目录和内部派发工具 `nino_runtime_dispatch_agent`，看不到订单查询、统计分析等业务 MCP Tool schema，也不加载业务 Reference。

Worker 是任务面。只有 Agent 和 Skill 被选中后，它才拿到：

- Agent 的角色约束；
- Skill 的执行说明；
- 按需读取的 Reference；
- Agent 与 Skill 双重白名单过滤后的 MCP 工具。

权限关系可以简化为：

```text
Worker 可执行工具
  = MCP 实际发现的工具
  ∩ Agent.allowed_tools
  ∩ Skill.allowed_tools
```

这不是为了让架构图更复杂，而是为了隔离上下文和权限。如果主 Agent 同时拥有所有业务说明和所有工具，业务越多，Prompt 越大，工具选择越模糊，越容易发生跨领域误调用。把控制面与任务面分开后，新增业务能力只需注册新的 Agent、Skill 和 MCP，不应修改 Orchestrator 的业务判断代码。

轻任务和复杂任务也不是同一种执行形态。当前 Nino Runtime 会为每个 Run 创建最小 Root Graph，用统一状态模型承接排除、澄清、普通回答和失败终态；只有命中业务能力并需要证据或独立验证时，Orchestrator 才把它扩展成 Specialist/Verifier DAG。也就是说，统一 Graph Truth 不等于每个问题都启动完整多 Agent 流程。

## 6. Skill 不是一段 Prompt，而是可执行策略包

Nino Agent 把业务知识放在 `agent/shared`，而不是写死在 Python Harness 中。一个 Skill 至少由两类文件组成：

```text
skills/nino-data-analysis/
├── SKILL.md                 # 给模型的工作说明
├── skill.json               # 给 Runtime/Harness 的机器约束
└── references/              # 按需加载的领域资料
```

`SKILL.md` 说明模型应该怎样工作；`skill.json` 声明允许的工具、最大步数、循环预算、References 和风险等级。自然语言负责表达策略，结构化清单负责强制执行。

这个区别很重要：写在 Prompt 里的“请不要调用写工具”只是建议，写在 allowlist 校验里的拒绝才是边界。同理，“尽量不要循环太久”不是预算，`max_steps`、`max_actions` 和 `timeout_seconds` 才是预算。

Reference 也不把文件路径直接交给模型。模型只能请求已登记的 Reference ID，Harness 再校验目录边界、文件存在性和字符上限，并记录内容哈希。这样既避免把所有领域资料一次性塞进上下文，也避免模型任意读取文件。

## 7. 一个循环必须知道怎样停下来

Agent 循环最危险的地方不是它不够聪明，而是它可能以不可预测的成本持续行动。初版通过 `LoopController` 统一限制 Orchestrator 与 Worker：

- `max_steps`：最多进行多少次模型决策；
- `max_actions`：最多执行多少个工具或派发动作；
- `timeout_seconds`：墙钟时间上限；
- `max_consecutive_failures`：连续失败阈值；
- `max_no_progress_steps`：无进展阈值；
- Action hash：拒绝相同名称、相同参数的重复动作。

预算采用“最严格值生效”：

```text
Worker 有效预算
  = min(Runtime hard limit, Agent limit, Skill limit)
```

低层配置只能收紧，不能突破平台上限。模型本身也不能修改计数器或停止状态，所有状态来自 Harness 观察到的真实执行结果。

停止也不是一个模糊的异常字符串。初版定义了稳定分类，例如 `final_answer`、`max_steps`、`max_actions`、`timeout`、`duplicate_action`、`policy_violation`、`dependency_error` 和 `cancelled`。这让 API、日志、评测和未来的其他语言实现可以共享语义。

## 8. Composition Root 把选择集中在一个地方

抽象如果没有清晰的装配位置，很容易变成散落全局的条件判断。最早版本把具体实现选择集中在 `bootstrap.py`：

```python
if settings.mode == "demo":
    model = DemoChatModel()
    tools = DemoToolClient()
elif settings.mode == "live":
    model = build_configured_model()
    tools = McpServerRegistry(configs)

worker = ReActHarness(...)       # lightweight
# 或 LangGraphReActHarness(...)  # langgraph

harness = OrchestratorHarness(model, skills, agents, worker_factory)
```

Demo 模式不是随手写的 mock。它让 API、路由、持久化、事件和循环可以在没有外部模型与 MCP 的情况下确定性运行。Live 模式替换掉的是 Port 背后的 Adapter，而不是上层业务流程。

这种装配方式还有一个长期收益：当某个框架能力真正需要时再引入它。项目可以先用可读、可控的轻量循环验证工具调用；需要图调度、分支或恢复时再选择 LangGraph，而不是一开始就让系统的领域契约依附于框架对象。

## 9. 第一版真正建立的，不是功能，而是演进空间

回看 `ee55b76`，它当然还不是最终形态。初版的 Orchestrator 本质上仍是“模型决定直接回答或顺序派发”；checkpoint 主要用于观测，还不能自动恢复；也没有持久化 TaskGraph、依赖节点、Gate 和图修订语义。

但它提前建立了几条很难事后补救的边界：

1. 模型决策与真实动作之间必须经过 Harness。
2. 任务生命周期与推理策略分属 Runtime 和 Harness。
3. 模型、工具、存储和执行框架通过 Port 替换。
4. 控制面只做能力选择，业务工具隔离在任务面。
5. 自然语言描述行为，机器清单强制权限与预算。
6. 每次执行产生结构化、持久化、可重放的事件。

这些边界让后续演进不必推翻第一版。TaskGraph 可以成为新的任务执行模型，Planner 可以从 Orchestrator 中拆出，Verifier 可以成为显式节点，React 客户端可以消费更细的流式进度，而 API、模型和 MCP 仍然处于各自的边界内。

## 10. 从这一章可以带走什么

如果正在把一个 Agent Demo 推向生产，不必照搬 Nino Agent 的目录，但可以先回答以下问题：

- 谁拥有 Conversation 和 Run 的生命周期？
- 谁能批准一次工具调用真正发生？
- 模型能看到哪些工具，权限来自哪里？
- 循环预算由谁计算，模型能否绕过？
- 每一步是否留下可持久化、可重放的状态？
- 替换模型 SDK 或执行框架时，哪些上层代码必须改？

如果这些答案都落在同一个 `agent.py` 或同一个 Graph 节点中，系统仍处于 Demo 阶段。Harness 工程化的第一步不是增加更多 Agent，而是让执行边界比模型行为更稳定。

下一章将沿 `9e67f80`、`be4427b` 和 `e7286f2` 展开：Nino Agent 如何把 Skill 编排、Loop checkpoint、权限校验与实时评测连成同一套执行证据，并从“能完成任务”走向“能证明任务是怎样完成的”。

---

## 代码考据

本文主要依据以下 Git 与代码位置：

- `ee55b76`：第一个完整项目提交，版本 `0.10.0`。
- `agent/python/src/framework/ports.py`：`AgentHarness`、`ChatModel`、`ToolProvider`。
- `agent/python/src/runtime/service.py`：Conversation/Run 生命周期与事件持久化。
- `agent/python/src/harness/orchestrator.py`：控制面循环与 Specialist 派发。
- `agent/python/src/harness/react.py`：Worker ReAct 执行与策略校验。
- `agent/python/src/harness/loop.py`：循环预算、计数与停止策略。
- `agent/python/src/bootstrap.py`：模型、工具与 Harness 的组合根。
- `agent/shared/`：Agent、Skill、Reference 与结构化契约。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
