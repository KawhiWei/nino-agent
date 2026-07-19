# Nino Agent 任务级 Harness 完整设计

> 适用版本：Nino Agent Runtime v0.16.0
>
> 当前代码基线：`175c506 feat: 优化历史追问与容器化部署`；Runtime 版本仍为 `0.16.0`
>
> 文档目标：解释 Nino Agent 为什么这样设计、代码如何实现、哪些能力已经成立，以及当前边界在哪里。

## 1. 一句话定位

Nino Agent 是一个面向只读业务数据分析的 API-first Agent Harness。它不把模型的一次回答当成任务
完成，而是使用持久化 Run、TaskGraph、通用受控 Worker、MCP Tool Observation 和独立 Verifier，
把一次用户请求执行成可约束、可观察、可验证、可恢复的任务。

当前最准确的产品表述是：

> Nino Agent 是一个以四个业务中立标准 Agent 为执行骨架，支持持久化任务图、确定性证据门禁、
> 独立验证和故障恢复的只读业务分析 Harness。

它已经能支撑订单查询、指标统计、异常分析和报表解释，但不是一个已经覆盖审批、写操作、身份、
审计、精确节点续跑和分布式调度的完整企业 Agent 平台。

## 2. 为什么不是普通的模型调用封装

普通数据问答通常只有以下流程：

```text
用户请求 User -> 提示词 Prompt -> 模型 Model -> 回答 Answer
```

这种方式无法回答几个关键工程问题：

1. 模型是否调用了真实数据能力，还是直接猜测？
2. 模型是否调用了不属于当前业务能力的 Tool？
3. 一个复杂请求拆成多个步骤后，谁保存任务状态？
4. 一个节点失败后，后续节点是否还会被错误执行？
5. “Agent 已经回答”和“结论已经验证”是否被错误地视为同一件事？
6. API 断线、进程重启或上下文过长后，任务如何继续？
7. 客户端怎样知道 Agent 正在路由、查询、验证，还是已经停止？

Nino Agent 的答案是：把模型放入 Harness，而不是把 Harness 放入 Prompt。

```text
用户请求 User Request
  -> 持久化运行 Durable Run
  -> 任务图控制面 TaskGraph Control Plane
  -> 总控路由 Orchestrator Route
  -> 规划器候选方案 Planner Candidate Plan
  -> 总控校验、持久化和调度 Orchestrator Validate / Persist / Schedule
  -> 通用分析工作节点 Generic Analyst ReAct Worker
  -> MCP 工具观察结果 MCP Tool Observation
  -> 证据门禁 Evidence Gate
  -> 通用验证节点独立重查 Generic Verifier Re-query
  -> 独立验证门禁 Independent Verification Gate
  -> 总控结果归并 Orchestrator Reconciliation
  -> 根任务完成 Root Completion
```

模型参与语义判断，但不能修改预算、伪造 Tool Observation、绕过能力目录、直接更新 TaskGraph 状态，
也不能把自己的结论直接标记成独立验证通过。

## 3. 设计范围

### 3.1 当前要解决的问题

- 以 REST + SSE 为 App、Web、Desktop 提供统一 Agent Runtime。
- 对订单、支付、退款、收入、成本、毛利、异常和报表类问题执行只读分析。
- Planner 根据动态 Agent + Skill 目录提出候选 TaskGraph。
- Orchestrator 确定性校验并接受 revision；只向 Worker 暴露 Skill 与 Agent 策略共同允许的 MCP Tools。
- 要求事实性回答至少存在一次成功的非 Reference Tool Observation。
- 对指定 Skill 的结果执行独立 Verifier 查询与结构化裁决。
- 对复杂请求保存 Node、依赖、Gate、Attempt 和结果。
- 支持不同 Conversation 并发、同一 Conversation 单活动 Run。
- 支持事件重放、取消、上下文压缩和进程重启后的安全恢复。

### 3.2 当前明确不解决的问题

- 业务写操作、退款执行、订单修改等副作用任务。
- 人工审批工作流。代码中存在 `awaiting_approval` 状态，但没有审批 API 和状态推进实现。
- 身份认证、租户隔离、RBAC、行列级数据权限。
- 完整审计与合规留存。
- 跨主机共享队列和远程数据库租约。
- 从模型调用或 Tool 调用中间位置恢复。
- 不重放 Root Orchestrator、直接从任意持久化 Ready Node 继续的精确 Resume。

这些边界是设计选择，不是文档遗漏。当前目标是把只读数据分析做正确，而不是提前实现通用工作流
平台的全部能力。

## 4. 核心设计原则

### 4.1 控制面与任务面分离

Orchestrator 是唯一控制面：负责路由、接受 Graph revision、持久化、调度、reconcile 和完成 Run。
Planner 是建议面，只看能力元数据与紧凑图状态，只能提出候选计划。Analyst/Verifier 是任务面：
加载业务 Skill、Reference 和 MCP Tool。

```text
控制面 Control Plane
  - 路由请求
  - 校验候选计划
  - 持久化任务图真相 Graph Truth
  - 调度节点
  - 执行门禁检查
  - 归并结果
  - 完成运行

建议面 Advisory Plane
  - 拆解用户目标
  - 提出候选节点、依赖、输入绑定和合同
  - 永远不能修改 Graph Truth

任务面 Task Plane
  - 加载业务指令
  - 调用只读 MCP 工具
  - 获取确定性结果
  - 生成有边界的结论
```

Planner 与 Orchestrator 都不直接获得业务 MCP Tool schema，因此不能绕过 Worker 权限边界查询数据。

### 4.2 TaskGraph 与 ReAct Loop 分离

TaskGraph 描述“任务之间是什么关系”；LoopController 描述“一个节点内部怎样执行”。两者不能合并成
一个无边界状态机。

| 状态层 | 负责内容 | 不负责内容 |
|---|---|---|
| TaskGraph | Node、依赖、Gate、Attempt、恢复和最终完成 | 模型消息、单次 Tool 调用细节 |
| ReAct Loop | model/tool/observation 循环、预算、停止原因 | 跨节点依赖、图级恢复 |
| Run/Event | API 状态、SSE、事件重放 | 业务 Prompt 和 Tool 权限决策 |
| Conversation | 多轮用户语义历史 | 当前 Graph 的执行授权 |

### 4.3 代码状态是真相，Prompt 只提供语义

以下规则由代码执行，而不是依赖模型自觉：

- Agent + Skill 组合必须来自动态 Capability Catalog。
- Tool 必须存在于 Agent 和 Skill allowlist 交集。
- 重复 Action、最大 step、最大 action、超时和连续失败由 LoopController 判断。
- DAG 重复 ID、未知依赖和环由 TaskGraphScheduler 拒绝。
- Node 执行前必须由 Repository 原子 claim。
- 完成 Node 必须有通过的 required Gate。
- Verifier `passed` 必须有真实 Tool evidence。
- TaskGraph 终态先持久化，Run 终态后发布。

### 4.4 证据与结论分离

Worker 的自然语言不是证据。证据来自成功的 Tool Observation；Verifier 也不能只阅读 Analyst 文本，
必须重新调用批准的只读 Tool。

### 4.5 先保守失败，再允许模型修正

缺少必要参数时使用结构化 clarification；Tool 暂时失败时可以在预算内选择不同 Action；重复调用、
越权调用、证据缺失、图结构非法和 Gate 失败则明确停止或重新规划，不把不确定结果包装成成功。

## 5. 总体架构

```mermaid
flowchart TB
    Client["客户端<br/>App / Web / Desktop"] --> API["接口层<br/>FastAPI REST + SSE"]
    API --> Runtime["运行时服务<br/>AgentRuntimeService"]
    Runtime --> Context["会话上下文管理器<br/>ConversationContextManager"]
    Runtime --> Repo["数据仓储接口<br/>AgentRepository"]
    Runtime --> Graph["持久化任务图控制器<br/>TaskGraphController"]
    Graph --> Repo
    Runtime --> Orchestrator["总控编排器<br/>OrchestratorHarness"]

    Orchestrator --> Catalog["能力注册表<br/>AgentRegistry + SkillRegistry"]
    Orchestrator --> Planner["建议型规划器<br/>PlannerHarness"]
    Orchestrator --> Scheduler["任务图调度器<br/>TaskGraphScheduler"]
    Orchestrator --> Factory["工作节点工厂<br/>worker_factory"]
    Factory --> Analyst["通用分析工作节点<br/>nino.analyst"]
    Factory --> Verifier["通用验证工作节点<br/>nino.verifier"]
    Planner --> Model["模型接口<br/>ChatModel Port"]
    Orchestrator --> Model
    Analyst --> Model
    Verifier --> Model
    Analyst --> References["参考资料提供器<br/>ReferenceProvider"]
    Verifier --> References
    Analyst --> Tools["工具接口<br/>ToolProvider Port"]
    Verifier --> Tools
    Tools --> Registry["多 MCP 注册表<br/>McpServerRegistry"]
    Registry --> MCP1["当前业务数据服务<br/>Nino Data MCP"]
    Registry --> MCPN["其他可选 MCP 服务<br/>Other MCP Servers"]

    Orchestrator -. "on_event 事件回调" .-> EventSink["运行时事件处理<br/>save_event"]
    EventSink --> Repo
    EventSink --> Graph
    Repo --> SQLite["SQLite 仓储实现<br/>SqliteAgentRepository"]
```

图中 Planner 不直接读取 Registry：Orchestrator 先根据 Agent/Skill 策略构造候选目录，再把候选元数据
传给 Planner。Orchestrator 也不直接写持久化 TaskGraph；Harness 事件通过 Runtime 注入的 `on_event`
回调先写 Event，再由 `TaskGraphController.record_event()` 投影为 Node、Gate 和 Attempt 状态。

### 5.1 代码目录映射

| 目录/文件 | 设计职责 |
|---|---|
| `src/api/app.py` | REST/SSE Host、生命周期启动和 API 映射 |
| `src/runtime/service.py` | Conversation/Run 生命周期、并发、取消、恢复调度、终态发布 |
| `src/runtime/task_graph.py` | Event 到 Graph 的投影、Node claim、Gate 和 Graph 收口 |
| `src/runtime/context.py` | token 预算、历史摘要、上下文编译 |
| `src/framework/ports.py` | AgentHarness、ChatModel、ToolProvider 等稳定 Port |
| `src/framework/task_graph.py` | TaskGraph、TaskNode、TaskGate、NodeAttempt、AcceptanceContract |
| `src/framework/loop.py` | Loop 状态、预算、停止原因的跨实现契约 |
| `src/harness/orchestrator.py` | 唯一控制面：路由、proposal 校验、Graph revision、调度、Gate 和最终归并 |
| `src/harness/planning.py` | Planner 模型边界、候选目录 Prompt 和结构化 plan-node Action |
| `src/harness/react.py` | 默认轻量 ReAct Worker |
| `src/harness/langgraph.py` | 同一 Harness Port 的 LangGraph Worker 实现 |
| `src/harness/scheduler.py` | DAG 验证和 Ready/Blocked 计算 |
| `src/harness/validation.py` | 持久化 TaskGraph 一致性 lint |
| `src/harness/agents.py` | 四类 Agent 角色、Skill 兼容策略、Tool policy 和委派权限 |
| `src/harness/skills.py` | Skill、路由元数据、Workflow/Assurance 元数据 |
| `src/harness/references.py` | Reference 白名单和安全文件读取 |
| `src/infrastructure/sqlite.py` | SQLite 表、事务、claim/lease、CAS 和恢复 |
| `src/infrastructure/mcp/registry.py` | 多 MCP 发现、Tool 路由、并发和熔断 |
| `src/bootstrap.py` | 唯一 composition root，选择 Model、Engine、Repository 和 MCP Adapter |

### 5.2 依赖方向

```text
接口层 api -> 运行时 runtime -> 框架接口 framework
编排层 harness -> 框架接口 framework
基础设施层 infrastructure -> 框架接口 framework
组装入口 bootstrap -> 所有具体适配器 all concrete adapters
```

`framework` 不引用 FastAPI、SQLite、httpx、LangChain、LangGraph 或 MCP SDK。Harness 不知道 SQLite
和 HTTP transport；Runtime 不负责 Prompt 和 Tool allowlist；Infrastructure 不决定业务流程。

## 6. 一次请求的完整执行过程

```mermaid
sequenceDiagram
    participant C as 客户端 Client
    participant A as 接口层 FastAPI
    participant R as 运行时 AgentRuntimeService
    participant DB as 仓储 AgentRepository
    participant G as 任务图 TaskGraphController
    participant O as 总控 OrchestratorHarness
    participant P as 规划器 PlannerHarness
    participant W as 分析节点 Analyst Worker
    participant T as 工具接口 MCP ToolProvider
    participant V as 验证节点 Verifier Worker

    C->>A: 提交消息 POST message
    A->>R: submit_message
    R->>DB: 原子写入消息和 queued Run
    R->>G: 创建根 Graph/Node/Gate
    R-->>A: 返回 queued Run 和 run_id
    A-->>C: 返回 202 + run_id
    R->>G: claim 根节点
    R->>DB: 读取历史或压缩上下文
    R->>O: run(user_input, history, on_event)
    O->>O: 正向召回和候选路由
    O->>P: 请求候选 TaskGraph revision
    P-->>O: 返回结构化候选节点
    O->>O: 确定性校验 proposal 和 DAG
    O-->>R: 回调 graph_planned 事件
    R->>DB: 持久化事件
    R->>G: 投影 graph_planned
    G->>DB: 持久化 Nodes/Gates/revision
    O-->>R: 回调 agent_started 并请求 claim
    R->>G: 投影并原子 claim 子节点
    O->>W: 执行任务、合同和绑定输入
    W->>T: 列出或调用允许的工具
    T-->>W: 返回 Tool Observation
    W-->>O: 返回归一化 Node Result
    O-->>R: 回调 agent_completed
    R->>G: 投影节点完成事件
    G->>DB: 提交 Node、Evidence Gate 和 Attempt
    O->>V: 独立验证 claim
    V->>T: 重跑批准的最小只读查询
    V-->>O: 返回结构化 evaluator verdict
    O-->>R: 回调 evaluator completed
    R->>G: 投影验证完成事件
    G->>DB: 提交 Verification Node/Gate/Attempt
    O->>O: 无工具归并已验证结果
    O-->>R: 返回最终 RunResult
    R->>G: 收口根 Graph
    G->>DB: 持久化根 Gate 和 Graph 终态
    R->>DB: 发布 Run 终态和 Assistant 消息
    R-->>A: 提供持久化事件和 Run 结果
    A-->>C: 返回 SSE 终态事件或 GET Run 结果
```

关键顺序是：Graph truth 先完成，Run terminal state 后发布。客户端看到 Run completed 时，持久化
TaskGraph、Node 和 Gate 已经能够解释为什么这个 Run 被认为完成。

Planner 不对应持久化 TaskNode，也没有独立 Graph 控制权。它在每个 Orchestration revision 中只做
一次候选计划决策；`graph_planned/graph_reconciled` 表示 Orchestrator 已经完成确定性校验并接受该
proposal，而不是 Planner 已经直接修改了 Graph。

仅依赖历史回答的追问走一条受限短路：Planner 只有在历史中存在 Assistant 回答时才能选择
`nino_runtime_answer_from_history`；Orchestrator 随后以无 Tool 模式，只基于先前已接受回答中的显式
事实生成 `history_reconciliation`。该分支不创建 Specialist/Verifier Node；如果历史不足，必须说明
需要新查询，不能补充外部事实。

## 7. 路由与能力目录

### 7.1 确定性路由第一层

Skill manifest 声明：

- `intent_keywords`：确定性候选召回。
- `routing.semantic_fallback`：关键词未命中时是否允许进入语义候选池。
- `capabilities`：向 Planner 暴露的能力摘要。
- `risk_level`：当前能力风险元数据。

每个 Skill 只描述自身支持的能力，不枚举其他 Skill 或系统不支持的意图。没有任何已注册能力匹配的
请求由 Planner 结构化拒绝。

### 7.2 受控语义路由第二层

关键词未命中时，只有 opt-in Skill 会进入候选。Planner 不能自由回答，只能选择：

1. `nino_runtime_submit_task_graph_node`：请求确实属于候选能力。
2. `nino_runtime_request_clarification`：缺少范围、日期或分组等关键信息。
3. `nino_runtime_answer_from_history`：仅解释、比较、改写或计算既有 Assistant 回答，不需要新数据。
   该 Action 只有存在 Assistant 历史时才会暴露。
4. `nino_runtime_reject_request`：请求不属于候选能力；只在语义 fallback 路径暴露。

这比“所有未命中请求都调用模型自由判断”更保守，也比“所有未命中请求立即拒绝”更能处理企业
同义表达。

### 7.3 Capability Catalog 内容

Planner 模型只看到元数据：

```json
{
  "agent_id": "nino.analyst",
  "agent_capabilities": ["data-analysis", "order-query"],
  "skill_id": "nino-data.analysis",
  "skill_capabilities": ["order-query", "grouped-statistics"],
  "risk_level": "read-only",
  "workflow_id": "business-analysis",
  "workflow_execution_shape": "adaptive",
  "assurance_mode": "strict_verify"
}
```

业务 Skill 正文、Reference 内容和 MCP Tool schema 不进入 Planner 或 Orchestrator 上下文。
Planner 只能提出候选图；Orchestrator 是唯一可以接受 revision、持久化 Graph Truth、调度 Node、
执行 reconcile 和完成 Run 的控制面。

四个标准角色的稳定边界：

| Agent | 输入 | 输出 | 禁止事项 |
|---|---|---|---|
| `nino.orchestrator` | 用户请求、候选 Skill、持久化/紧凑 Node 状态 | 接受的 revision、调度决策、最终回答 | 业务 MCP、把 proposal 当 Graph Truth |
| `nino.planner` | 用户目标、会话历史、候选能力元数据、紧凑 Node outcome | 候选节点、依赖、binding、AcceptanceContract，或澄清/拒绝/仅历史回答控制决定 | 持久化、调度、MCP、直接生成最终回答 |
| `nino.analyst` | 委派任务、选定 Skill、合同、binding、Reference | 证据化 Node Result | Root 规划、独立验证声明、写操作 |
| `nino.verifier` | 原任务、合同、Analyst claim、选定 Skill | 结构化 verdict | 把 Analyst 文本当证据、修补原结论、写操作 |

## 8. Agent、Skill、Workflow、Reference 和 Tool

| 对象 | 回答的问题 | 当前实现 |
|---|---|---|
| Agent | 谁承担这个节点的职责？ | 总控、规划、通用分析和通用验证 Agent |
| Skill | 通用 Worker 应如何完成这类业务节点？ | 指令、Tool 白名单、Reference、预算 |
| Workflow metadata | 任务倾向使用什么执行形态？ | `adaptive/single_node/graph`，当前随 Skill 加载 |
| Assurance metadata | 结果需要什么评价？ | `best_effort/strict_verify` + required evaluators |
| Reference | 当前步骤需要加载什么受控知识？ | 指标定义、订单规则、异常规则、报表格式 |
| Tool | 读取哪个确定性外部能力？ | MCP 工具定义和调用（Tool definitions and invocation） |

当前没有独立 Workflow Registry/Compiler。Workflow 和 Assurance 已从正文中抽成机器元数据，但它们
仍然随 Skill 加载。对于当前单一数据分析场景，这比提前建设通用 Workflow 平台更合适。

## 9. TaskGraph 领域模型

### 9.1 任务图（TaskGraph）

TaskGraph 表示一个用户 Run 的宏观任务真相：

```text
TaskGraph
  id / run_id / conversation_id
  user_intent
  status
  version
  parent_graph_id / relation_type
  metadata / timestamps
```

状态：`pending -> running -> completed|failed|cancelled`。未完成的旧计划节点还可进入
`superseded`；Schema 中保留 `awaiting_approval`，但当前
没有审批状态推进实现。

### 9.2 任务节点（TaskNode）

Node 是语义工作边界，而不是每一个 Tool Call：

- `orchestration`：Root 路由、接受计划、调度、Gate 和归并；Planner 本身不单独持久化 Node。
- `specialist`：一个有独立结果的业务任务。
- `verification/review/critique`：Evaluator 节点。
- `approval`：Schema 预留，当前未执行。

Node 保存 owner、依赖、AcceptanceContract、结构化 Result、错误和时间。

每个可执行 Node 还保存两类身份：`logical_node_id` 是 Planner/用户可理解的稳定名称；物理 `id` 对应
某个已接受的具体版本。`node_fingerprint` 对 Agent、Skill 版本、任务、context、依赖指纹、binding、
AcceptanceContract 和 supersedes 关系做规范化 SHA-256。Completed 复用必须同时匹配逻辑 ID 和
Fingerprint。

### 9.3 任务门禁（TaskGate）

Gate 是节点能否被接受的检查点，不是执行者：

- `acceptance`：Root 完成检查。
- `evidence`：Specialist 是否有成功 Tool Observation。
- `independent_verification`：独立 Verifier 是否给出 proved/pass。
- `review/critique`：可扩展 Evaluator 类型。
- `approval`：Schema 预留，当前未执行。

Gate 状态：`pending/passed/failed/blocked`。

### 9.4 节点执行尝试（NodeAttempt）

Attempt 记录一次节点执行授权：

```text
attempt_number
status: running/completed/failed/cancelled/interrupted
lease_owner / lease_expires_at
checkpoint
error_code
```

恢复不会覆盖旧 Attempt；中断历史被保留，新执行创建递增 Attempt。

### 9.5 验收合同（AcceptanceContract）

合同定义 Gate 通过后节点可以诚实声称什么：

```json
{
  "spec_source": "user_request+registered_skill:nino-data.analysis",
  "target_outcome": "统计 2026 年 7 月毛利",
  "positive_checks": ["结果直接满足委派任务"],
  "negative_checks": ["没有 Tool 证据时不得声称事实"],
  "evidence_requirements": ["至少一次成功业务 Tool Observation"],
  "gaps": [],
  "pass_label": "business_result_verified"
}
```

Planner 应提供任务专属合同；未提供时 Harness 生成保守默认合同。Orchestrator 接受后，合同同时传给 Worker、Verifier 和
持久化 TaskNode，避免三处使用不同的完成定义。

## 10. Graph 规划、依赖和并行

### 10.1 任务图修订版（Graph revision）

Planner 一次决策可以提交一个或多个结构化候选 Specialist 节点。Orchestrator 逐项校验并将 Skill
要求的 Evaluator 节点自动展开：第一次接受后记录 `graph_planned`；失败结果需要修复时，再次调用
Planner，并将新接受的未来节点记录为 `graph_reconciled`。完成历史不删除，Planner 必须只提出新的
pending/repair work，不能重复已完成节点。

当前 Planner Action 是逐 Node 的 `nino_runtime_submit_task_graph_node`；一次 ModelTurn 中的多个
Action 共同构成一个候选 revision。revision 编号由 Orchestrator Loop 分配，Planner 无权指定持久化
版本或直接调用 Repository。

持久化 revision metadata 包含单调递增 revision、`revision_id`、`parent_revision_id`、reason、实际
接受的物理 Node ID 和 Fingerprint 映射。Root replay 即使再次从模型 step 1 开始，也不会覆盖已有
revision 编号。

### 10.2 Completed 冻结、Fingerprint 和 supersedes

同一个 logical node（逻辑节点）重新出现时：

```text
fingerprint 相同（可执行身份完全一致）
  -> 复用原已完成节点结果 Completed Node Result，不创建新的执行尝试 Attempt

fingerprint 不同（同一逻辑节点的新执行版本）
  -> 冻结旧 Completed 节点的 status/result/gate
  -> 创建带新物理 ID 的节点
  -> Runtime 自动在新节点 metadata 记录 supersedes_node_id 版本 lineage
  -> 不把旧 Completed 状态改回 Pending/Failed
```

Fingerprint 包含 Skill version，因此业务规则升级后不会误用旧结果；依赖 Fingerprint 也会进入下游
身份，因此上游合同变化会使下游旧结果失去复用资格。历史 Completed 仍保留用于解释和审计，不会被
重置成 Pending。

显式 repair（修复节点）是另一种情况。Planner 在替换失败或 blocked 工作时必须提交
`supersedes_node_id`；Orchestrator 只允许它指向早期 revision 中可替代的失败/阻塞节点，并禁止 repair
同时依赖被替代节点。TaskGraphController 接受 revision 后，将旧节点和所有尚未 Completed 的受影响
下游标记为 `superseded`，Gate 标记 `blocked`，并保存
`superseded_by_node_id/invalidated_by_node_id`。

因此，同 logical ID 的 Fingerprint 变化由 Runtime 自动建立版本 lineage；不同 logical ID 的 repair
是否允许显式 supersede，则由 Orchestrator 和 TaskGraphController 按历史状态共同校验。两者不能混用。

如果 Specialist 工作本身已经 Completed，但独立 Assurance Gate 失败，Completed 工作仍不可被显式
supersede。Orchestrator 向 Planner 暴露 `work_status=completed`、`assurance_status=failed` 和
`supersedable=false`；Planner 应自动提出新的独立只读 repair Node，不设置 `supersedes_node_id`，也不
依赖原 Completed Node。即使模型错误提交了该 supersedes 关系，Orchestrator 也会在确定性校验前移除。

### 10.3 DAG 验证

TaskGraphScheduler 在执行前检查：

- Node ID 格式。
- 同一 revision ID 唯一。
- 依赖必须来自本 revision 或已知历史节点。
- 依赖图不能包含环。

Orchestrator 还检查候选 Action 名称、Agent/Skill pair 是否属于目录、Task 是否非空、Node ID 是否与
历史重复，以及每个 `input_binding` 的 source/selector 是否有效。模型 proposal 只是非可信输入。

Repository 的 claim 才是最终执行授权。Scheduler 的 Ready 结果是调度决策，不代替数据库事务。

### 10.4 波次执行

没有未满足依赖的 Node 进入 Ready wave；互不依赖的 Node 使用 `asyncio.gather` 并行，但仍受
`NINO_GRAPH_MAX_PARALLEL_NODES` 限制。上游失败后，下游标记为 skipped，Gate 标记 blocked。

### 10.5 控制依赖与数据依赖

`depends_on` 只表达控制关系。`input_bindings` 表达下游需要消费的上游字段：

```json
{
  "name": "upstream_metrics",
  "source_node_id": "summary-query",
  "selector": "outputs"
}
```

允许 selector：`summary/outputs/findings/evidence/concerns/recommended_next`。Binding source 必须同时
出现在 `depends_on`。没有显式 binding 时，Harness 默认传递依赖节点 summary。

这样下游得到的是裁剪后的结构化输入，而不是父 Agent 的完整上下文、隐藏推理或原始 Tool dump。

## 11. 通用 Analyst/Verifier 工作节点 ReAct

### 11.1 Worker 输入

Worker fresh context 由以下部分组成：

```text
Agent 角色指令 Agent instructions
Skill 业务指令 Skill instructions
严格工作节点策略 Strict worker policy
委派任务 Delegated task
验收合同 Acceptance contract
节点结果合同 Node result contract
绑定的上游输入 Bound upstream inputs
按需加载的批准参考资料 Approved references
```

### 11.2 Tool 目录

Worker 可见 Tool 集合：

```text
全局 MCP 工具目录 global MCP catalog
  交集 Skill.allowed_tools
  交集 Agent 角色兼容策略
  加上允许的内部动作 internal Actions
```

当前通用 Analyst/Verifier 使用：

```text
accepted_risk_levels = [read-only]
tool_policy = selected-skill-only
```

`selected-skill-only` 不是任意 Tool 通配符。它表示 Agent 不再复制每个业务 Skill 的 Tool 名称；有效
Tool 仍必须同时出现在 MCP discovery 和当前 `Skill.allowed_tools` 中，并且 Skill 的 risk/capability
必须被 Agent role policy 接受。显式 `allowed_skills/allowed_tools` 仍作为兼容模式保留。

缺失任何必需 Tool 时，Worker 在模型调用前失败，不能静默降级成自由回答。

### 11.3 内部 Actions

- `nino_runtime_load_reference`：按 ID 加载 Skill Reference。
- `nino_runtime_request_clarification`：提交不超过 500 字符的缺参问题。
- `nino_runtime_submit_evaluator_verdict`：Evaluator 结构化终态。
- `nino_runtime_delegate_agent`：只在 Agent 定义允许且深度预算未超限时出现。

### 11.4 证据门禁（Evidence Gate）

事实性回答必须至少有一次成功、非 Reference、非内部 Action 的 Tool Observation。否则返回
`EVIDENCE_REQUIRED`。这阻止模型在数据 Tool 没有执行时凭训练知识生成看似合理的业务数字。

### 11.5 Node Result 归一化

推荐 Worker 返回：

```json
{
  "status": "completed",
  "summary": "结论摘要",
  "outputs": {"currency": "CNY", "margin": 60},
  "findings": ["毛利为正"],
  "concerns": [],
  "recommended_next": []
}
```

Harness 另外记录 Tool evidence、error_code 和 retryable。为兼容现有模型，纯文本结果仍会被归一化成
最小结构化 Result；当前没有强制 Worker 必须调用独立的 submit-node-result Action。

## 12. Loop 工程约束

### 12.1 预算合并

Worker 使用 Runtime、Agent、Skill 三层中最严格的预算：

```text
最大步骤数 max steps
最大动作数 max actions
超时时间 timeout
最大连续失败数 max consecutive failures
最大无进展步骤数 max no-progress steps
```

模型不能扩大预算。

Planner 当前不是独立 ReAct Loop：每个 Root revision 只允许一次 Planner ModelTurn，它与最终
reconciliation 一起计入 Orchestration Loop 的 step/action/timeout 预算。Planner manifest 保留统一
Agent 配置字段，但不会据此创建第二套控制循环。

### 12.2 Action 与 Observation

```text
开始步骤 begin_step
  -> 检查超时和最大步骤 timeout / max-step check
  -> 获取模型决定 model decision
  -> 校验工具调用 validate tool call
  -> 登记动作签名 register_action(signature)
  -> 调用工具 invoke tool
  -> 记录成功或失败观察结果 record_observation
  -> 继续或停止 continue or stop
```

Action signature 使用规范化参数计算 hash。重复签名被拒绝，checkpoint 不保存完整参数和秘密。

### 12.3 检查点（Checkpoint）

产生阶段：

- 模型调用前：`before_model`
- Observation 记录后：`after_observation`
- 循环终止时：`terminal`

Checkpoint 用于进度、诊断和事件重放。它不是模型中间态 Resume：不保存隐藏 chain-of-thought、完整
Tool 参数、API Key 或可重建的全部协议消息。

## 13. 独立验证与 Gate

Skill 通过 `assurance.required_evaluators` 声明需要哪些评价角色。当前数据分析 Skill 使用：

```json
{
  "assurance": {
    "mode": "strict_verify",
    "required_evaluators": ["verification"]
  }
}
```

Verifier 的约束：

1. 使用 fresh context。
2. 接收原任务、AcceptanceContract 和 Analyst claim。
3. 不把 Analyst 文本当作事实。
4. 重新调用最小必要只读 Tool。
5. 通过 `nino_runtime_submit_evaluator_verdict` 返回结构化 verdict。
6. 只有 `verdict=passed`、`evidence_level=proved` 且存在 Tool evidence 才通过。

Evaluator 结构化裁决（verdict）：

```json
{
  "verdict": "passed",
  "evidence_level": "proved",
  "checked_requirements": ["订单号和金额与 Tool 结果一致"],
  "failed_requirements": [],
  "concerns": []
}
```

需要准确理解“确定性 Gate”的边界：Gate 的状态转换和证据存在性是确定性的；业务语义是否真正正确
仍取决于 Tool 的确定性、AcceptanceContract 的质量和 Verifier 的判断。当前不是形式化证明系统。

## 14. 持久化和一致性

### 14.1 SQLite 表

```text
conversations
messages
runs
run_events
run_event_counters
runtime_instances
conversation_contexts
task_graphs
task_nodes
task_gates
node_attempts
```

### 14.2 关键事务保证

- user message 与 queued Run 原子创建。
- 数据库唯一索引限制同一 Conversation 只能有一个 queued/running Run。
- Event sequence 使用 `BEGIN IMMEDIATE` 原子分配。
- Node claim 使用事务检查 Node 状态、依赖和 required Gate，然后创建 Attempt。
- Node、Gate、Attempt 在一个事务中收口。
- TaskGraph 使用 version compare-and-swap，冲突返回 `GRAPH_VERSION_CONFLICT`。
- Graph/Gate 终态先写入，Run completed 后发布。

### 14.3 为什么 Event 和 Graph 都存在

Event 是发生过什么的时间序列；Graph 是当前任务控制状态。`graph_planned`、`agent_started`、
`tool_completed` 和 `agent_completed` 事件由 TaskGraphController 投影为 Node、Gate 和 Attempt 变更。

两者用途不同：

- Event 支持 SSE、断线重放和诊断。
- Graph 支持调度、恢复、Gate 判断和 API 查询。

## 15. 恢复语义

### 15.1 当前已经实现的恢复

Runtime 启动时：

1. 注册 `runtime_instance` 并开始心跳。
2. 查找 lease owner 已失效或 lease 已过期的 running Attempt。
3. 将旧 Attempt 标记为 `interrupted/RUNTIME_RESTARTED`。
4. 将对应 Node、Graph 和 Run 返回 pending/queued。
5. 读取原始 trigger message 和 Conversation history。
6. 重新运行 Root Orchestrator。
7. 当模型生成相同稳定 logical Node ID 且 Fingerprint 一致时，复用已完成 Node Result，不再次运行 Worker。
8. 未完成 Node 创建递增 Attempt 后重新执行。

第 7 步还必须通过 Fingerprint 校验。只有 Agent、Skill version、任务、合同、binding 和依赖身份均
一致才复用；不一致时冻结旧 Completed 并创建替代 Node。这是一种适合当前只读任务的
至少一次 Root 重放（at-least-once Root replay）加 Fingerprint 安全的 Completed Node 复用。

### 15.2 当前没有实现的精确 Resume

- 不直接从持久化 Graph 的任意 Ready Node 开始。
- 不恢复某次模型调用中间状态。
- 不恢复某次 Tool 调用中间状态。
- 不保证模型重放时生成完全相同的未来 Graph revision。
- 不支持有副作用 Tool 的幂等恢复。

因此对外应该说“支持只读任务的安全恢复”，不应该说“支持任意节点的精确 Resume”。

## 16. Conversation 与上下文

原始 Conversation messages 是权威历史；`conversation_contexts` 是可以重建的派生摘要。

```text
历史 token 数 <= 预算
  -> 使用完整历史 full history

历史 token 数 > 预算
  -> 原样保留最近消息
  -> 对较早消息做提取式摘要
  -> 持久化摘要和 through_message_id 游标

下一次运行 next run
  -> 复用摘要和游标之后的消息
  -> 只有组合上下文再次溢出时才压缩新增部分
```

摘要作为带明确标记的普通历史消息注入，不提升为系统指令，降低历史内容成为新指令的风险。

当前是确定性提取式压缩，不额外调用模型；优点是成本和行为稳定，缺点是语义压缩能力有限。

多轮历史还有一条受限执行语义：如果当前追问只需要解释、比较、改写或计算先前已接受 Assistant 回答
中的显式事实，Planner 可选择 `nino_runtime_answer_from_history`。Orchestrator 使用无 Tool 的独立
history reconciliation；历史不足时只能说明需要新查询，不能用该路径绕过 Evidence Gate 补充新事实。

## 17. 多 MCP 设计

McpServerRegistry 把多个 MCP Server 聚合成一个 ToolProvider：

1. 并行发现各 Server Tool。
2. 建立 `tool_name -> server_id` 路由。
3. 全局 Tool 名称冲突时拒绝目录。
4. required Server 失败时阻断发现。
5. optional Server 失败时隔离该 Server。
6. 每个 Server 使用独立 semaphore 限制并发。
7. 连续调用失败达到阈值后打开熔断器。

Registry 只解决 transport 和可用性，不承担业务授权；Skill allowlist 与 Agent role policy 的交集仍在
Harness 中执行。

## 18. API 和事件模型

### 18.1 产品协议

当前产品入口是 REST + SSE，不是 CLI，也不是 ACP。

主要资源：

- 会话（Conversation）
- 消息（Message）
- 运行实例（Run）
- 事件（Event）
- 上下文快照（Context snapshot）
- 任务图、节点、门禁和执行尝试（TaskGraph / Node / Gate / Attempt）
- Skill、Agent 和 MCP 状态

### 18.2 Run 状态

```text
排队 queued -> 运行中 running -> 已完成 completed | 失败 failed | 已取消 cancelled
```

同一 Conversation 同时只有一个 active Run；不同 Conversation 共享 Runtime 全局并发配额。

### 18.3 关键事件

```text
运行启动：run_started
任务图已规划或已修订：graph_planned / graph_reconciled
循环检查点：loop_checkpoint
Skill 已选择：skill_selected
模型启动或完成：model_started / model_completed
参考资料已加载：reference_loaded
工具启动或完成：tool_started / tool_completed
Agent 启动、完成或失败：agent_started / agent_completed / agent_failed
请求补充信息：clarification_requested
策略拒绝：policy_rejected
节点跳过：node_skipped
回答增量：answer_delta
运行完成、失败、取消或中断：run_completed / run_failed / run_cancelled / run_interrupted
```

客户端可使用 `after` 或 SSE `Last-Event-ID` 续接事件，不需要从零重放。

## 19. TaskGraph 一致性检查（Lint）

`lint_task_graph` 检查持久化状态，而不是只检查模型计划：

- 依赖是否指向存在 Node。
- 每个 Node 是否有 required Gate。
- completed Node 的 required Gate 是否全部 passed。
- running Node 是否恰有一个 running Attempt。
- 非 running Node 是否错误保留 running Attempt。
- 持久化依赖图是否存在环。

API：`GET /api/v1/runs/{run_id}/task-graph/lint`。

## 20. 扩展一个新业务能力

以新增“客户经营分析”为例：

1. 新增 MCP Server 或在现有 Server 增加确定性只读 Tool。
2. 在 `agent/shared/skills/<skill>/skill.json` 声明 ID、capabilities、routing、Tool、Reference、预算和 assurance。
3. 编写同目录 `SKILL.md`，描述执行规则，不写 transport 细节。
4. 确认 Skill 的 `risk_level/capabilities` 与 `nino.analyst`、所需 Evaluator 的兼容策略匹配。
5. 在 `NINO_MCP_SERVERS` 注册 Server。
6. 新增固定 question bank，记录 `derived_from`、Golden facts、期望 Tool/Reference 和边界结果。
7. 增加 routing、Planner proposal、越权拒绝、正常 Tool、缺参澄清、Evaluator/Gate 和 API 事件测试。
8. 重启 Runtime，使动态 Capability Catalog 重新加载 Skill 与 MCP Tool。

默认不创建业务专用 Agent，也不修改四个标准 Agent manifest。只有出现以下情况才新增 Agent：任务不再
属于通用只读分析、需要新的执行权限/风险级别、需要不同上下文隔离语义，或引入 `review/critique`
等当前 Verifier 无法承担的评价职责。业务名称不得进入 Orchestrator、Planner、Analyst、Verifier ID。

## 21. 测试策略和证据

当前 Python 测试覆盖层次如下：

当前工作区完整单元测试共 73 项；历史章节中的“62 项”是对应旧提交当时的数量，不代表当前测试总数。

| 测试文件 | 主要证明 |
|---|---|
| `test_layered_architecture.py` | 依赖方向、Port 边界和 step 契约 |
| `test_react_harness.py` | Tool allowlist、证据门禁、澄清、重复调用和预算 |
| `test_langgraph_harness.py` | LangGraph 与相同 Harness Port 的 model-tool-model 流程 |
| `test_orchestrator.py` | Planner/Orchestrator 分离、无 MCP 规划、DAG、并行、binding、reconcile、Verifier、历史追问、Assurance 修复和节点复用 |
| `test_task_graph_scheduler.py` | 未知依赖、环、Ready/Blocked 计算 |
| `test_task_graph.py` | API Graph、Gate、Attempt、CAS、claim、Fingerprint 复用、supersedes、下游失效和恢复 |
| `test_persistence_context.py` | 多轮历史、摘要持久化和增量复用 |
| `test_mcp_registry.py` | 多 Server、冲突、optional/required 隔离、熔断 |
| `test_adapters.py` | OpenAI-compatible Tool Calling 和 MCP HTTP Adapter |
| `test_api.py` | REST/SSE、取消、TaskGraph 和终态 |
| `test_evaluation_suite.py` | Skill 标准题库契约 |

标准命令：

```bash
cd agent/python
PYTHONPATH=src .venv/bin/python -m unittest discover -s tests -v
```

固定标准题库位于 `agent/shared/question-banks/<capability>/`，由所有语言 Runtime 共享。测试运行时
不得临时生成题目；Skill、Tool、指标口径或种子真值变化时，人工更新预期并提升题库版本。Live
benchmark 位于 `agent/python/evals/live_benchmark.py`，用于真实模型和 MCP 链路；单元测试通过不
等于真实模型路由、参数和解释质量已经达到生产要求。

### 21.1 `0.14.0` 真实自然语言链路证据

使用本地 `live + lightweight + native` Runtime，通过 REST 提交题库原文：

```text
查询订单 DEMO-202607-001，给出收入、成本、退款和毛利，并说明数据来源。
```

实际事件和持久化结果：

```text
nino.planner 执行规划 planning
  -> 提交候选节点 order_financials_demo_202607_001
Orchestrator 接受 graph_planned revision
  -> nino.analyst 加载 Skill/References 并调用 nino_data_get_order_detail
  -> 证据门禁 evidence Gate 通过
  -> nino.verifier 独立重查并提交 proved/pass
  -> 独立验证门禁 independent_verification Gate 通过
  -> nino.orchestrator 无工具归并 reconciliation (tools = empty)
  -> TaskGraph 完成 -> Run 完成
```

最终确定性数据为收入 `225.00 CNY`、成本 `165.00 CNY`、成功退款 `0.00 CNY`、演示口径毛利
`60.00 CNY`，公式为 `225 - 165 - 0 = 60`。该案例证明的是自然语言能够通过四角色边界完成真实
Model + Skill + Reference + MCP + Gate 链路；它不替代其余标准题库和持续 Eval。

## 22. Git 演进历史

### 22.1 `4fd8492`：仓库占位

- 时间：2026-07-17 11:37 +08:00。
- 内容：临时占位文件。
- 设计意义：无。后续初始化提交删除该占位文件。

### 22.2 `ee55b76 feature： 初始化项目`

这是第一版完整系统基线，建立了：

- FastAPI REST + SSE Runtime。
- Framework/Harness/Runtime/Infrastructure 分层。
- lightweight 和 LangGraph 两种 Worker。
- Orchestrator、Analyst、Verifier Agent 定义。
- Skill、Reference、Agent JSON/Markdown 共享契约。
- 多 MCP Registry、OpenAI-compatible 和 LangChain Adapter。
- SQLite Conversation/Run/Event/Context。
- LoopController、Tool evidence 和基础委派。
- .NET 数据 MCP、PostgreSQL schema、种子和验证 SQL。
- Docker Compose 和初始设计文档。

这一阶段解决的是“Agent 怎样运行”和“业务数据怎样通过 MCP 暴露”。

### 22.3 `9e67f80 feat: 强化技能编排并新增实时评测套件`

这一阶段把“能调用工具”提高到“受控地调用正确工具”：

- 严格 Skill 路由、委派和 Tool evidence 策略。
- 缺参必须使用结构化 clarification。
- 固定 GPT-5.4 live 配置和 Tool Calling 接入。
- 新增标准 question bank、evaluation suite 和 live benchmark。
- 扩展 MCP 汇总 totals 与相关测试。

这一阶段解决的是“怎样证明 Agent 没有绕过 Skill 和证据要求”。

### 22.4 `be4427b feat: 完善企业级数据 Agent Harness 执行内核`

这一阶段把单 Run Loop 提升为任务级 Harness：

- TaskGraph、TaskNode、TaskGate、NodeAttempt 持久化模型。
- DAG 验证、Ready wave、并行 Node 和依赖失败阻断。
- AcceptanceContract、Node Result、Evaluator Verdict 和 Graph schema。
- 独立 Verifier Gate，不再把 Analyst 自述当验收。
- Node claim/lease、Graph CAS、原子 Event sequence。
- Runtime heartbeat、interrupted Attempt 和 Root replay 恢复。
- 受控语义 fallback、顶层 clarification/rejection。
- `input_bindings` 和上游结构化结果传递。
- 多 MCP 并发与熔断、TaskGraph API 和 lint。
- 62 项 Python 回归测试通过。

这一阶段解决的是“怎样让一个复杂业务请求成为可保存、可调度、可验证和可恢复的任务”。

### 22.5 `e7286f2` 与 `f13ed93`：统一设计和真实评测

- 将总体设计收敛为一份权威任务级 Harness 文档。
- 使用 PostgreSQL 12.18 确定性数据集和固定标准题库。
- 增加真实 REST、模型、MCP、Reference、Verifier 和 Gate 验收链路。

### 22.6 Runtime `0.14.0`：Planner 与通用 Worker 分离

- 新增独立 `nino.planner` 和 `PlannerHarness`，只产出候选 Graph Node。
- Orchestrator 不再承担模型规划，保留唯一 Graph 控制权和无 Tool 最终归并。
- `nino-data.analyst/verifier` 收敛为业务中立的 `nino.analyst/verifier`。
- 新业务通过 Skill、MCP、Reference 和固定题库扩展，不再默认复制 Agent。
- Agent schema 新增 risk/capability compatibility 与 `selected-skill-only` Tool policy。
- 完整 Python 回归测试保持 62 项通过，并通过真实自然语言订单分析验证四角色事件链。

该阶段已提交为 `facbc9e feat: 拆分 Planner 并统一业务中立 Harness 架构`。

### 22.7 Runtime `0.15.0`：严格复用和 Reconcile Lineage

- Node Fingerprint 纳入 Skill version、合同、binding、context 和依赖 Fingerprint。
- 相同 logical ID 但不同 Fingerprint 会创建新物理 Node，旧 Completed 结果冻结。
- repair Node 使用 `supersedes_node_id` 显式替代失败/blocked 历史。
- 未完成的受影响下游进入 `superseded`，Gate 进入 blocked。
- revision 保存 parent lineage、reason、accepted Node 和 Fingerprint 映射。
- 仍采用 Root replay，不宣称任意 Ready Node 精确续跑。

`22.1` 至 `22.7` 来自已提交 Git 历史。

### 22.8 Runtime `0.15.1`：固定题库驱动的控制流修复

- 合法澄清是控制流终态：以 `clarification_requested` 作为专用控制证据完成 Node，不启动 Verifier。
- 其他 Runtime 内部 Action 仍不能满足业务事实 Evidence Gate。
- Skill 排除词阻止明确的非业务概念问题进入数据分析候选集。
- 汇总 MCP 直接返回 `negativeMarginOrderCount`，模型不得把分页异常条数当成总数。
- 异常 Top N 优先使用确定性 reason codes，避免无必要的逐单查询耗尽 Action 预算。

### 22.9 Runtime `0.16.0`：安全的最终回答流式传输

- Native OpenAI-compatible Adapter 使用 `stream=true`，增量聚合文本、reasoning 和 Tool Call 参数。
- Planner、Analyst、Verifier 的流只在 Harness 内聚合，不对最终用户暴露。
- 最终 Orchestrator reconciliation 的文本分片投影为持久化 `answer_delta` Run Event。
- 前端继续复用 `/api/v1/runs/{run_id}/events/stream`，无需新增另一套流式接口。
- 流式改善首字延迟和连接活性，但不绕过模型 read timeout 或 Harness Loop Deadline。

### 22.10 Runtime `0.16.0`：Web 客户端、历史追问与 Assurance 修复

- `1236bc7` 新增 React 会话客户端和单请求 SSE，支持 active Run 断线恢复与任务取消。
- `175c506` 新增 `nino_runtime_answer_from_history`，仅允许基于先前已接受回答处理无需新数据的追问。
- Specialist 已 Completed 但独立验证失败时，紧凑状态明确标记
  `work_status=completed/assurance_status=failed/supersedable=false`。
- 该 Assurance 修复必须创建独立只读 repair Node；不能显式 supersede 或依赖原 Completed Node。
- 新增 React 多阶段镜像、Nginx SSE 反向代理和 Compose `web` 服务。
- 当前完整 Python 单元测试为 73 项。

## 23. Nino Agent 的完整工程团队模型。

| 维度 | Nino Agent |
|---|---|
| 任务对象 | 数据查询、统计、异常和报表解释 |
| Worker 核心 | ReAct + read-only MCP |
| 证据 | 确定性数据 Tool Observation |
| 任务图 | 业务分析 Node 和 Verifier |
| 当前重点 | 数据准确、口径、权限边界、恢复 |

因此当前不继续建设独立 Workflow Registry、Artifact Store、复杂 Reviewer/Critic 拓扑和通用工作流
编译器。已有 TaskGraph 是为了支持数据分析中的批量、依赖、验证和恢复，而不是把 Nino 变成 Code
Agent。

## 24. 已实现、部分实现和尚未实现

| 能力 | 状态 | 准确说明 |
|---|---|---|
| ReAct + MCP | 已实现 | lightweight/LangGraph，Tool allowlist 和 evidence gate |
| 独立 Planner | 已实现 | 候选 Node proposal；无 MCP、持久化、调度和最终回答权限 |
| 四个业务中立 Agent | 已实现 | Orchestrator/Planner/Analyst/Verifier，Skill-first 业务扩展 |
| 多轮会话 | 已实现 | SQLite 原始消息 + 派生摘要 |
| TaskGraph | 已实现 | Node/Gate/Attempt、依赖、并行、revision |
| Fingerprint-safe reuse | 已实现 | 合同、Skill version、binding 和依赖不变时才复用 Completed |
| Reconcile lineage | 已实现 | parent revision、supersedes、Pending 后缀失效和 Completed 冻结 |
| Assurance 失败修复 | 已实现 | Completed Worker 保持冻结，创建无 supersedes/依赖关系的独立只读 repair Node |
| 仅历史追问 | 已实现 | 只使用先前已接受回答，无 Tool；需要新事实时仍走 Worker 和 Gate |
| 确定性 Evidence Gate | 已实现 | 成功 Tool Observation 是硬条件 |
| 独立 Verifier | 已实现 | proved/pass + Tool evidence |
| AcceptanceContract | 已实现 | 节点合同贯穿 Worker、Verifier 和持久化 |
| 结构化 Node Result | 部分实现 | 支持并归一化，但未强制 submit Action |
| Workflow/Assurance | 部分实现 | manifest 元数据已存在，无独立 Registry/Compiler |
| 恢复 | 部分实现 | Root replay + completed-node reuse，仅适合只读任务 |
| 精确 Resume | 未实现 | 不能直接从任意 Ready Node 或中间 Tool 状态继续 |
| 审批 | 未实现 | 只有状态/schema 预留，没有 API 和推进逻辑 |
| 写操作安全 | 未实现 | 无 approval、idempotency key、Action ledger、compensation |
| 身份和租户 | 未实现 | 无 Auth/RBAC/row-level permission |
| 分布式 Runtime | 未实现 | SQLite lease 只适用于共享本地文件场景 |
| React Web 客户端 | 已实现 | 单请求 SSE、断线重放、active Run 恢复、取消和 Compose/Nginx 部署 |

## 25. 设计取舍

### 25.1 为什么默认 lightweight，而不是所有流程都用 LangGraph

核心契约是 AgentHarness Port 和 LoopController，不是某个框架。lightweight 依赖少、行为透明、便于
测试；LangGraph 作为可选 Worker Engine 验证框架可替换性。宏观 TaskGraph 是 Nino 自己的持久化业务
模型，不应与 LangGraph 内部 model/tool graph 混为一谈。

### 25.2 为什么使用 SQLite

当前目标是单机可运行、状态真实、测试快速。SQLite 足以验证事务、CAS、claim、lease 和恢复语义。
远程共享数据库是部署演进，不应在核心语义尚未稳定时成为前置条件。

### 25.3 为什么 Verifier 默认独立查询

Analyst 的文本可能遗漏条件或解释错误。Verifier 重查最小必要数据，才能让“验证”具有独立性。代价是
增加模型和 Tool 调用，因此后续可按 Skill 风险调整 assurance，但当前财务数据演示选择严格验证。

### 25.4 为什么保留自由文本 Node Result 兼容

强制结构化 Action 能提高确定性，但会增加模型适配和 Prompt 复杂度。当前先通过 Harness 归一化建立
稳定下游契约，再根据真实 Eval 决定是否强制 submit-node-result，避免提前增加无验证收益的机制。

### 25.5 为什么恢复只承诺只读安全

Root replay 可能再次规划未完成节点。只读查询重复执行通常安全；写操作可能重复创建订单或退款。
没有幂等 Action ledger 之前，开放写 Tool 会使恢复语义不可信，因此当前明确限制为 read-only。

## 26. 如何向别人解释这套思路

可以使用以下四句话：

1. **Planner 负责建议，Orchestrator 负责决定。** 候选计划是非可信输入，只有确定性校验后才能进入 Graph Truth。
2. **Tool Observation 才是数据证据。** Agent 没有成功查询数据就不能生成事实性结论。
3. **Agent 完成不等于任务完成。** Analyst 结果还要经过独立 Verifier 和持久化 Gate。
4. **恢复的是任务，不是隐藏思维。** 系统保存 Graph、Node、Gate、Attempt、revision 和 Fingerprint，通过 Root replay 与严格节点复用恢复，只读范围内安全。
5. **Completed 历史不可改写。** Assurance 失败时创建独立修复节点，不把已完成业务证据改回失败状态。
6. **历史追问不是证据后门。** 只允许重述或计算既有回答；任何新事实仍需 Tool Observation。

更完整的介绍：

> Nino Agent 把一次自然语言业务问题建模成持久化 Run 和 TaskGraph。Orchestrator 先确定候选 Skill，
> 再让业务中立 Planner 提出候选节点；只有通过 Agent/Skill、DAG、binding 和合同校验的 proposal 才会
> 成为 Graph Truth。通用 Analyst 加载选定 Skill 和 Reference，只调用 MCP discovery、Skill allowlist
> 与 Agent 只读策略共同允许的 Tool。需要严格保证的 Skill 会自动创建通用 Verifier 节点，Verifier
> 独立重查并提交结构化 verdict。最终只有 Orchestrator 可以在所有 Gate 通过后，以无 Tool 模式归并
> 回答。所有 Node、Gate、Attempt 和事件都被持久化，进程中断后可重放 Root 并复用已完成节点。

## 27. 相关文档

- `README.md`：多语言项目定位、当前实现矩阵和快速入口。
- `agent/python/README.md`：运行、API 和配置入口。
- `doc/gpt-5.4-agent-runbook.md`：真实模型联调与验收。

本文件是当前唯一的总体设计文档。历史计划和分散的专题设计已删除；后续架构变化应直接更新本文，
避免多个文档分别描述同一执行语义。
