# 第六章：企业 Harness 为什么不能属于某个 Agent 框架

> Nino Agent Harness 工程实践（六）
> 当前版本：`0.16.0`
> 主题：框架中立 Runtime、可替换 Worker 与企业执行治理

Nino Agent 默认不使用 LangChain 和 LangGraph。当前默认组合是：

```text
NINO_MODEL_ADAPTER=native
NINO_AGENT_ENGINE=lightweight
```

这不代表系统不需要模型适配、状态循环、任务图或恢复能力。相反，Nino 已经自行实现了这些能力中属于企业 Runtime 和 Harness 的部分，并把 LangChain、LangGraph 放在可替换的基础设施位置。

这背后有一个核心判断：

> Conversation、Run、权限、证据、验收、事件、TaskGraph 和恢复都是企业业务事实，不应该由某个 Agent 框架的数据类型决定。

## 1. 先把三个层次分开

LangChain、LangGraph 和 Nino Agent 并不完全处于同一层。

```text
企业 Agent Runtime / Harness
  -> 生命周期、业务状态、权限、证据、验收、持久化和产品协议

Agent 执行引擎
  -> 模型与工具之间如何分支、循环和更新局部状态

模型与 AI 组件 Adapter
  -> 如何连接具体 Provider、消息协议、Tool schema 和生态组件
```

在 Nino 中，这三层分别对应：

| 层次 | Nino 代码 | 主要职责 |
|---|---|---|
| Runtime/Harness | `runtime/`、`harness/orchestrator.py`、`framework/task_graph.py` | Run、TaskGraph、规划、调度、Gate、Attempt、恢复 |
| Worker Engine | `harness/react.py`、`harness/langgraph.py` | Specialist 内部的 model-tool-model 循环 |
| Model Adapter | `infrastructure/openai_compatible.py`、`infrastructure/langchain_model.py` | 模型协议与 Nino 领域类型转换 |

LangChain 当前只参与第三层；LangGraph 当前只作为第二层的一种实现。第一层由 Nino 自己拥有。

## 2. 哪些 Runtime 事实与框架无关

一次企业 Agent 请求进入服务端后，会产生一组需要长期保持稳定的事实：

- Conversation 和消息属于哪个业务会话；
- Run 何时排队、开始、取消、失败或完成；
- 当前请求匹配哪个 Skill；
- Planner 提交了哪些候选节点；
- 哪个 proposal 被 Orchestrator 接受；
- Node 由谁领取，处于第几次 Attempt；
- Tool 调用是否符合 Agent/Skill 权限；
- 节点获得了哪些业务证据；
- Acceptance Contract 是否满足；
- Verifier Gate 是否通过；
- 哪个 revision 是当前有效版本；
- 哪些历史结果被复用、替代或失效；
- 客户端可以重放哪些 Event；
- 服务重启后应从哪个任务边界继续。

这些事实无论由普通 Python 循环、LangGraph、其他工作流引擎还是未来的独立 Worker 执行，都不能改变语义。

如果数据库直接保存某个框架的 Graph State，API 直接返回框架 Message，前端事件直接依赖框架 callback，那么更换执行引擎就不再是 Adapter 替换，而会变成数据库、接口和产品行为的整体迁移。

## 3. Nino 用自己的 Port 隔离框架

Nino 在 `framework/ports.py` 中定义了三个关键协议：

```python
class AgentHarness(Protocol):
    async def step(self, state: HarnessStepState) -> ModelTurn: ...
    async def run(...) -> RunResult: ...

class ChatModel(Protocol):
    async def complete(self, messages, tools) -> ModelTurn: ...

class ToolProvider(Protocol):
    async def list_tools(self) -> Sequence[ToolDefinition]: ...
    async def invoke(self, call: ToolCall) -> ToolResult: ...
```

Runtime 只认识 Nino 自己的 `Message`、`ModelTurn`、`ToolCall`、`AgentEvent` 和 `RunResult`。它不知道 LangChain 的 `AIMessage`，也不知道 LangGraph 的 `StateGraph`、`Command` 或节点状态。

框架对象只允许存在于 Adapter 或具体 Engine 内部。这条边界保证以下内容保持稳定：

```text
REST/SSE API
SQLite schema
Run Event 协议
TaskGraph 领域模型
Skill/Agent manifest
评测题库与验收规则
React 客户端状态机
```

对企业系统而言，框架中立首先不是技术洁癖，而是控制迁移成本。

## 4. LangChain 在 Nino 中做什么

`LangChainChatModel` 的职责很窄：

1. 使用 `ChatOpenAI` 连接模型服务；
2. 把 Nino `Message` 转成 LangChain Message；
3. 把 Nino `ToolDefinition` 绑定成 Tool schema；
4. 把 LangChain 响应转换回 Nino `ModelTurn` 和 `ToolCall`。

```text
Nino Message / ToolDefinition
  -> LangChainChatModel
  -> ChatOpenAI
  -> LangChain response
  -> Nino ModelTurn / ToolCall
```

项目没有把 LangChain 的 Chain、AgentExecutor、Memory、Retriever 或 callback runtime 作为主执行链。选择 `NINO_MODEL_ADAPTER=langchain` 只替换模型 Adapter，不改变 Orchestrator、Worker 策略、TaskGraph、Repository 和 API。

LangChain 对 Nino 的主要价值是生态接入：Provider、Retriever、Document loader、向量库和结构化输出等组件。如果未来确实需要这些能力，可以在 Infrastructure 层使用，而不必让其类型向 Runtime 扩散。

## 5. LangGraph 在 Nino 中做什么

`LangGraphReActHarness` 把 Worker 内部循环表达成显式图：

```text
START
  -> model_node
      -> 有 Tool Call：tool_node -> model_node
      -> 有最终回答：END
      -> 违反策略：END with error
```

它与 `ReActHarness` 实现相同的 `AgentHarness` 契约，并复用同一组：

- Skill 和 Agent 权限；
- Tool allowlist；
- `LoopController` 与预算；
- Reference 和 clarification Action；
- Evidence Gate；
- Evaluator Verdict；
- Event 和 `RunResult` 协议。

因此 LangGraph 在当前 Nino 中不是宏观控制面，也不拥有业务 TaskGraph。它只负责单个 Analyst/Verifier Worker 内部的模型与工具状态流转。

## 6. Nino 为什么还保留 lightweight Worker

`ReActHarness` 使用普通 Python 实现同一个闭环：

```text
模型决策
  -> 校验 Tool Call
  -> 注册 Action 与预算
  -> 执行 Tool
  -> 写入 Observation
  -> 再次调用模型
  -> Evidence Gate
  -> 最终回答
```

它已经覆盖当前简单 ReAct Worker 所需的核心能力：

- 消息状态累积；
- 最大 step/action 和超时；
- 重复 Action 检测；
- 连续失败和无进展停止；
- 取消；
- Tool/Reference/内部 Action 路由；
- Loop checkpoint；
- 结构化事件；
- 证据与权限门禁。

对当前执行形态而言，lightweight 路径依赖少、调用栈直接、事件顺序清晰，更容易设置断点和定位 Tool Calling 问题。因此 Compose 固定使用 `live + lightweight + native`，不会因为安装了可选框架就改变默认行为。

自研 lightweight 的价值是保留最小可靠路径，不是重新建设一套通用 AI 框架。

## 7. 两种“图”解决的不是同一个问题

Nino TaskGraph 和 LangGraph StateGraph 都有“节点、状态和流转”，但它们的生命周期和所有权不同。

| 维度 | Nino TaskGraph | Worker StateGraph |
|---|---|---|
| 表达对象 | 用户业务任务 | 单个 Worker 的执行步骤 |
| 生命周期 | 跨模型调用、跨 Attempt、跨服务重启 | 一次 Worker 执行 |
| 典型节点 | specialist、verification、root orchestration | model、tool |
| 持久化内容 | Contract、Gate、Result、revision、fingerprint、lease | messages、calls 和局部执行状态 |
| 谁可以修改 | Runtime 根据受控 Event 事务化投影 | Worker Engine 按图规则更新 |
| 主要消费者 | 调度、恢复、审计、API、运营和前端 | Agent 执行器 |
| 完成标准 | 证据与验收 Gate 通过 | 到达 END 或返回局部结果 |

宏观 TaskGraph 不应该由 Worker Engine 的临时状态替代。到达 `END` 只说明这次图运行结束，不自动证明业务结果获得证据、通过独立验证或可以发布给用户。

## 8. 企业 Harness 为什么必须拥有治理语义

LangGraph 可以用节点、middleware 和 checkpointer组合出许多治理能力，但企业平台不能把治理是否存在交给每条业务 Workflow 自觉实现。

Nino 把以下规则提升为 Harness 一等语义：

- Agent、Skill、Tool 三层能力边界；
- Planner proposal 必须经过确定性校验；
- Runtime hard limit 不能被下层扩大；
- 事实型结果必须存在业务 Tool evidence；
- Worker 完成、节点验收和 Graph 完成是不同状态；
- strict assurance 必须展开独立 Verifier；
- Node 执行必须经过 Repository claim；
- Completed 复用必须匹配 fingerprint；
- 失败后先 reconcile，再生成新 revision；
- Graph terminal 必须先于 Run terminal 持久化；
- 事件不能包含凭据和隐藏推理。

这些规则对所有 Worker Engine 生效。否则同一个 Skill 使用 lightweight 时有 Evidence Gate，切换到 LangGraph 后却可以绕过，平台就不存在真正统一的治理边界。

## 9. 框架中立对企业落地的六个价值

### 9.1 产品协议稳定

前端消费的是 Nino Run Event 和 TaskGraph API，而不是框架 callback。更换模型或 Worker Engine 后，客户端不需要重写。

### 9.2 持久化状态可长期演进

数据库保存业务 Node、Gate、Attempt 和 revision，不保存难以迁移的第三方运行对象。Schema 演进由产品语义驱动，而不是跟随框架版本。

### 9.3 治理规则不会因引擎不同而分叉

权限、预算、证据、验收、敏感信息过滤和停止原因由 Harness 统一定义。框架只能执行已经批准的局部状态流转。

### 9.4 供应商与版本风险可控

Provider SDK、LangChain 和 LangGraph 都可以升级、替换或暂时移除。企业核心执行记录不会被依赖升级绑架。

### 9.5 测试可以围绕业务契约

同一套测试可以对 lightweight 和 LangGraph 检查 Skill 选择、Tool 证据、事件顺序和 RunResult，而不只测试某个图是否到达 END。

### 9.6 能按任务复杂度选择引擎

简单 Worker 使用透明的 Python Loop；出现复杂分支、节点级中断、人工审批或精确恢复需求时，可以引入更强执行引擎。平台不必为所有请求支付相同复杂度。

## 10. Nino 当前相对通用框架的优势在哪里

Nino 的优势不是拥有更多通用组件，而是已经形成面向企业执行的控制语义：

| 能力 | Nino 当前落点 |
|---|---|
| 框架中立领域类型 | `framework/models.py`、`ports.py` |
| 持久化业务 TaskGraph | Graph、Node、Gate、Attempt、revision |
| 执行治理 | allowlist、预算、停止原因、重复检测 |
| 业务验收 | Acceptance Contract、Evidence Gate、Evaluator Verdict |
| 并发控制基础 | Node claim、lease、Graph CAS、Event sequence |
| 安全复用 | logical ID、fingerprint、superseded/invalidation |
| 产品投影 | REST/SSE、可重放 Event、流式阶段与最终回答 |
| 引擎可替换 | lightweight/LangGraph Worker，native/LangChain Model Adapter |

这些能力让 Nino 更接近“业务任务系统加 Agent 控制平面”，而不只是模型调用工作流。

## 11. 哪些地方不应该宣称已经超过 LangGraph

框架中立不等于所有框架能力都应自研。LangGraph 在以下领域拥有更成熟的通用能力：

- 节点级 checkpoint；
- 任意节点 interrupt/resume；
- Human-in-the-loop；
- durable execution；
- 子图组合；
- 状态 reducer；
- 图级 streaming；
- tracing、可视化和调试生态；
- 大量经过验证的工作流模式。

Nino 当前 `loop_checkpoint` 是持久化的观察与诊断快照，不是完整 Worker 协议续点。Runtime 能在进程重启后中断旧 Attempt、重放 Root，并安全复用 fingerprint 相同的 Completed Node；但不能从 Worker 某个 model/tool 节点精确恢复全部消息与副作用状态。

LangChain 在 Provider、Retriever、Document loader、向量库和 AI 组件集成方面也有明显生态优势。没有必要为“零依赖”重复实现所有连接器。

企业 Harness 应该自己拥有治理和业务事实，同时有选择地复用成熟框架能力。

## 12. 四种组合证明了边界是否成立

Nino 的组合根支持：

```text
native model + lightweight worker
LangChain model + lightweight worker
native model + LangGraph worker
LangChain model + LangGraph worker
```

真正重要的不是组合数量，而是切换后以下契约仍然成立：

- API 请求和响应不变；
- Conversation、Run 和 Event schema 不变；
- Skill、Agent 和 Tool 权限不变；
- Loop budget 和停止语义不变；
- Evidence Gate 和 Acceptance Contract 不变；
- TaskGraph、Gate、Attempt 和 revision 不变；
- 前端进度与最终回答协议不变。

如果切换引擎后这些业务事实发生变化，说明框架已经穿透了 Harness 边界。

## 13. 什么时候应该使用 LangChain 或 LangGraph

选择框架应该由缺失能力和工程成本决定。

适合继续使用 `native + lightweight`：

- 模型服务提供稳定的 OpenAI-compatible 协议；
- Worker 主要是受控 model-tool-model 循环；
- 当前最重要的是调试透明度、权限和证据；
- 不需要 Worker 内部任意节点的精确恢复。

适合引入 LangChain Adapter：

- 需要它已有的 Provider 或 AI 组件集成；
- 希望减少特定模型消息和 Tool schema 的适配工作；
- 能保证转换在 Infrastructure 边界内完成。

适合增强 LangGraph Worker：

- Worker 内部出现较复杂的条件分支和子图；
- 需要节点级 interrupt、checkpoint 或 Human-in-the-loop；
- 需要复用成熟的图执行、tracing 和可视化生态；
- 仍由 Nino Runtime 掌握业务 Graph Truth 和最终验收。

## 14. 最终定位

Nino 不需要把自己定位成 LangChain 或 LangGraph 的替代品。更准确的定义是：

> Nino Agent 是一个框架中立、以治理和持久化业务 TaskGraph 为核心的企业 Agent Runtime；模型组件和 Worker 执行引擎可以按需接入。

Nino 自己负责：

```text
可信边界
业务状态
权限与预算
证据与验收
任务版本与恢复
持久化与产品协议
```

可选框架负责：

```text
模型和 AI 生态适配
Worker 内部状态流转
节点级恢复与人工介入
通用 tracing 和图执行工具
```

企业 Harness 的核心竞争力不是选中了哪个 Agent 框架，而是无论底层框架如何变化，平台仍然知道：谁可以做什么、事实从哪里来、结果为什么被接受、失败后从哪里继续。

---

## 代码考据

- `agent/python/src/framework/ports.py`：框架中立的 Harness、Model 和 Tool Port。
- `agent/python/src/framework/models.py`：Nino 自有 Message、Tool、Event 和 Result 类型。
- `agent/python/src/bootstrap.py`：Model Adapter 与 Worker Engine 组合根。
- `agent/python/src/harness/react.py`：默认 lightweight ReAct Worker。
- `agent/python/src/harness/langgraph.py`：相同 Harness 契约的 LangGraph Worker。
- `agent/python/src/infrastructure/langchain_model.py`：LangChain 模型 Adapter。
- `agent/python/src/runtime/service.py`：框架之外的 Run 生命周期与持久化。
- `agent/python/src/runtime/task_graph.py`：框架之外的业务 Graph Truth 投影。
- `agent/python/tests/test_langgraph_harness.py`：LangGraph 可选引擎契约测试。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
