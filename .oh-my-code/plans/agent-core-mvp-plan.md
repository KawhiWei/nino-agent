# Agent Core MVP 方案与阶段计划

## 目标与边界

### MVP 目标
围绕“可对话执行企业业务操作”的 Agent Core 能力先跑通最小闭环：用户发起对话，系统创建会话并持久化消息，Agent 进行规划/工具调用/回复，多 Agent 以最小编排方式协作，必要时通过 MCP 调用外部工具，并在上下文过长时进行会话压缩。

### 明确不做
- 不实现登录、注册、SSO、企业 IAM、RBAC、组织/租户管理。
- 不实现复杂企业审批流、完整工单系统、审计合规平台。
- 不追求完整 ACP 协议实现；首版只实现项目内部可演进的最小消息/执行契约。
- 不追求完整多 Agent 平台；首版只支持固定角色 Agent 的顺序/路由式协作。

### MVP 成功标准
- 可以创建一个匿名或本地开发用户会话。
- 可以发送消息并获得 Agent 回复。
- 消息、Agent 执行步骤、工具调用结果可持久化并回放。
- 上下文超过阈值时可压缩历史并继续对话。
- 至少一个 MCP 工具可被 Agent 调用并把调用轨迹写入存储。
- ACP/MCP/多 Agent 均有最小落地点，后续可扩展但不过度平台化。

## Agent Core 核心能力拆分

1. **Conversation Runtime 会话运行时**
   - 创建/读取/追加会话。
   - 管理消息流：user、assistant、system、tool、agent-event。
   - 维护当前上下文窗口与压缩摘要。

2. **Agent Runtime Agent 执行内核**
   - 接收用户消息与会话上下文。
   - 选择主 Agent 执行。
   - 输出回复、计划、工具调用请求和执行事件。

3. **Tool Runtime 工具运行时**
   - 抽象工具注册、参数校验、执行、结果写入。
   - 首版优先接 MCP 工具；本地 mock/tool 也可作为开发兜底。

4. **Multi-Agent Orchestration 最小多 Agent 编排**
   - 固定角色：Coordinator Agent + Worker Agent，可选 Summarizer Agent。
   - 首版采用顺序编排或简单路由，不做复杂 DAG、市场化插件、自动团队生成。

5. **ACP-lite 执行契约**
   - 定义内部 Agent 消息、任务、工具调用、事件、结果的统一 schema。
   - 用于隔离前端、Agent Runtime、工具层和后续协议扩展。

6. **Memory / Context Manager 记忆与上下文管理**
   - 从持久化消息中构建模型上下文。
   - 根据 token/轮次数阈值压缩旧消息。
   - 保留关键事实、任务状态、工具结果和用户偏好。

7. **Persistence 持久化层**
   - 存储会话、消息、压缩摘要、Agent run、tool call。
   - 提供按会话回放、继续对话、调试执行轨迹能力。

## 聊天记录存储数据模型建议

### 核心表/集合

#### conversations
- `id`: 会话 ID。
- `title`: 会话标题，可由首条消息或摘要生成。
- `status`: active / archived / deleted。
- `metadata`: JSON，保存业务上下文、前端来源等。
- `created_by`: 预留用户 ID，MVP 可固定为 `anonymous` / `dev-user`。
- `created_at`, `updated_at`。

#### messages
- `id`: 消息 ID。
- `conversation_id`: 所属会话。
- `role`: user / assistant / system / tool / agent。
- `content`: 文本内容。
- `content_type`: text / markdown / json / tool_result。
- `agent_name`: 产生消息的 Agent，可为空。
- `run_id`: 关联 Agent 执行 ID，可为空。
- `tool_call_id`: 关联工具调用 ID，可为空。
- `parent_message_id`: 支持未来分支/重试。
- `metadata`: JSON，保存 token、模型名、trace 信息、前端展示信息。
- `created_at`。

#### agent_runs
- `id`: Agent 执行 ID。
- `conversation_id`: 所属会话。
- `trigger_message_id`: 触发本次执行的用户消息。
- `agent_name`: coordinator / worker / summarizer 等。
- `status`: queued / running / succeeded / failed / cancelled。
- `input_snapshot`: JSON，执行时的上下文摘要或消息 ID 列表。
- `output_message_id`: 最终回复消息。
- `error`: 错误信息。
- `created_at`, `completed_at`。

#### tool_calls
- `id`: 工具调用 ID。
- `run_id`: 所属 Agent run。
- `conversation_id`: 冗余便于查询。
- `tool_provider`: mcp / local。
- `tool_name`: 工具名。
- `arguments`: JSON 参数。
- `result`: JSON 或文本结果。
- `status`: requested / running / succeeded / failed。
- `error`: 错误信息。
- `created_at`, `completed_at`。

#### conversation_summaries
- `id`: 摘要 ID。
- `conversation_id`: 所属会话。
- `summary_type`: rolling / checkpoint / task_state。
- `covered_message_from`, `covered_message_to`: 覆盖的消息范围。
- `content`: 摘要正文。
- `facts`: JSON，关键事实。
- `open_tasks`: JSON，未完成任务。
- `tool_findings`: JSON，重要工具结果。
- `token_estimate`: 估算 token。
- `created_at`。

### 设计原则
- 消息是事实源，摘要是派生数据；摘要可重建。
- 工具调用与 Agent run 单独建模，便于调试和回放。
- 所有鉴权相关字段只预留，不参与 MVP 逻辑。
- `metadata` 用于承载早期不稳定字段，但核心查询字段应显式建列。

## 会话压缩/上下文管理设计建议

### 上下文构建顺序
1. 系统提示词与 Agent 角色说明。
2. 最近的 rolling summary / task_state summary。
3. 最近 N 轮原始消息。
4. 当前用户消息。
5. 必要的工具调用结果摘要，而不是完整大结果。

### 压缩触发条件
- 消息总 token 估算超过模型上下文预算的 60%-70%。
- 会话轮数超过固定阈值，例如 12-20 轮。
- 工具结果过大，单条结果超过阈值时立即提炼。
- 用户显式要求“总结/继续这个上下文”。

### 压缩产物结构
- 当前目标。
- 已确认事实。
- 用户偏好/约束。
- 已执行操作与工具结果。
- 未完成事项。
- 风险/待澄清问题。
- 最近对话中不能丢失的原文片段引用。

### MVP 策略
- 首版使用滚动摘要：保留最近 N 轮原文 + 旧消息摘要。
- Summarizer Agent 可以作为固定内部 Agent，不暴露给用户。
- 摘要失败时降级为扩大最近消息窗口或提示用户开启新会话。
- 不做长期记忆、向量检索、跨会话画像；仅预留后续 memory adapter。

## ACP、MCP、多 Agent 首版最小落地方式

### ACP-lite
首版定义内部统一对象，不追求外部协议兼容完整度：
- `AgentTask`: task_id、conversation_id、input_message_id、context_refs、required_capability。
- `AgentEvent`: run_started、thought/planning、tool_requested、tool_completed、message_created、run_completed、run_failed。
- `AgentResult`: status、assistant_message、tool_calls、summary_updates、handoff_target。

验收重点是内部边界清晰，后续可以映射到正式 ACP。

### MCP
首版只接入一种 MCP 工具路径：
- 支持配置一个 MCP server 或一个 mock MCP adapter。
- Agent 可列出可用工具、选择工具、传参调用、接收结果。
- tool_calls 表记录完整调用轨迹。
- 工具调用失败时 Agent 能返回可理解错误，而不是崩溃。

### 多 Agent
首版建议固定三类 Agent：
- `CoordinatorAgent`: 默认入口，理解用户意图、决定直接回复或调用工具/Worker。
- `WorkerAgent`: 执行具体任务，例如工单草稿、查询、结构化信息提取。
- `SummarizerAgent`: 内部压缩上下文。

首版编排方式：
- 不做自由协商。
- 不做复杂并行。
- 由 Coordinator 进行显式 handoff 或顺序调用。

## 推荐开发顺序与阶段验收

### Phase 1: Agent Core 骨架与 ACP-lite 契约
**目标:** 先定义运行时边界和内部协议，让后续模块按同一契约接入。

**范围:**
- 定义 Conversation Runtime、Agent Runtime、Tool Runtime、Context Manager 的模块边界。
- 定义 ACP-lite schema：AgentTask、AgentEvent、AgentResult、ToolCall。
- 提供一个最小 CoordinatorAgent，可接收消息并返回静态/模型回复。

**验收标准:**
- 可以从一条用户输入创建 AgentTask。
- Agent Runtime 可以产出 AgentEvent 与 AgentResult。
- CoordinatorAgent 可生成一条 assistant 消息。
- 不依赖登录态即可运行。

### Phase 2: 聊天记录与执行轨迹持久化
**目标:** 让对话和 Agent 执行可保存、可回放、可调试。

**范围:**
- 落地 conversations、messages、agent_runs、tool_calls 的最小数据结构。
- 实现创建会话、追加消息、查询消息列表。
- Agent run 与消息关联。

**验收标准:**
- 新建会话后发送 3 轮消息，刷新/重启后仍可读取完整记录。
- 每次 Agent 回复都能找到对应 agent_run。
- 没有登录系统时，`created_by` 使用固定 dev/anonymous 标识。

### Phase 3: MCP 工具调用闭环
**目标:** Agent 能调用至少一个工具，并将工具结果纳入回复。

**范围:**
- 实现 MCP adapter 或 mock MCP adapter。
- Tool Runtime 负责工具列表、参数传递、结果返回、错误捕获。
- Agent 可基于用户意图触发工具调用。

**验收标准:**
- 用户提出需要工具的问题时，Agent 触发一次 tool_call。
- tool_calls 中记录 provider、tool_name、arguments、result/status。
- 工具成功时，assistant 回复包含工具结果解释。
- 工具失败时，assistant 返回可理解失败原因，agent_run 标记为 failed 或 succeeded_with_error。

### Phase 4: 会话压缩与上下文管理
**目标:** 长会话可以在压缩后继续，不丢关键状态。

**范围:**
- Context Manager 按预算选择 system prompt、摘要、最近消息、当前消息。
- SummarizerAgent 生成 rolling summary。
- 存储 conversation_summaries。

**验收标准:**
- 构造超过阈值的长会话后自动生成 summary。
- 后续 Agent 回复能引用压缩前的关键事实。
- summary 记录覆盖的 message 范围。
- 原始 messages 不被删除。

### Phase 5: 最小多 Agent 编排
**目标:** 从单 Agent 运行升级到固定角色多 Agent 协作。

**范围:**
- CoordinatorAgent 判断是否需要 WorkerAgent 或 SummarizerAgent。
- WorkerAgent 返回结构化结果给 Coordinator。
- Agent handoff 通过 ACP-lite AgentEvent/AgentResult 表达。

**验收标准:**
- 一个用户请求可产生 Coordinator -> Worker -> Coordinator 的执行链。
- 每个 Agent run 都被单独记录。
- 最终 assistant 回复由 Coordinator 汇总。
- 不引入复杂权限、组织、Agent 市场等平台化能力。

### Phase 6: MVP 联调与演示场景固化
**目标:** 将上述能力串成可演示的 Agent Core MVP。

**范围:**
- 固化一个企业业务操作示例，如“帮我整理工单提交内容/生成工单草稿”。
- 前端只需要最小聊天入口、会话列表、消息详情、工具调用轨迹展示。
- 补充失败路径和降级策略。

**验收标准:**
- 能完成：创建会话 -> 多轮对话 -> 工具调用 -> 持久化 -> 压缩 -> 继续对话。
- 演示不需要登录。
- 可展示消息历史、Agent run、tool_call、summary。
- 已知限制以文档形式列出。

## 后续登录/权限预留方式

### 预留但不实现
- 数据表保留 `created_by`、`tenant_id`、`visibility`、`metadata.auth_context` 等字段，但 MVP 不做校验。
- Runtime 接口接收可选 `actor` / `request_context` 参数，默认 anonymous。
- Tool Runtime 预留 `permission_check(tool, actor, context)` hook，MVP 直接 allow。
- Conversation 查询接口预留 owner/tenant 过滤参数，MVP 固定默认值。
- AgentEvent 预留审计字段：actor_id、trace_id、source。

### 禁止在 MVP 中提前实现
- 不做 RBAC 规则引擎。
- 不做企业组织架构。
- 不做 SSO/OAuth/SAML。
- 不做多租户隔离策略。
- 不做管理员后台。

## 执行计划步骤

### Step 1: 固化 Agent Core MVP 边界与 ACP-lite 契约
**What:** 将 MVP 范围限定为 Agent Runtime、Conversation Runtime、Tool Runtime、Context Manager，并定义 AgentTask、AgentEvent、AgentResult、ToolCall 最小 schema。
**Agent:** executor
**References:** 本计划“目标与边界”“ACP、MCP、多 Agent 首版最小落地方式”。
**MUST NOT:** 不引入登录、权限、企业 IAM、多租户、复杂审批流。
**Verify:** 产出一份 schema/模块边界文档，并逐项覆盖 AgentTask、AgentEvent、AgentResult、ToolCall；确认文档中登录/权限标记为 deferred。
**Parallel:** 必须先于 Phase 2-5；可与产品演示场景梳理并行。

### Step 2: 落地聊天记录与执行轨迹模型
**What:** 按 conversations、messages、agent_runs、tool_calls、conversation_summaries 建立 MVP 持久化模型和读写接口计划。
**Agent:** executor
**References:** 本计划“聊天记录存储数据模型建议”。
**MUST NOT:** 不把权限逻辑写进 MVP 查询路径；只保留字段。
**Verify:** 能用模型描述一轮 user -> assistant、一次 tool_call、一次 summary 的完整关联关系。
**Parallel:** 依赖 Step 1；可与 Step 3 的工具 adapter 详细设计并行。

### Step 3: 实现 MCP 工具调用最小闭环计划
**What:** 规划 Tool Runtime 与 MCP adapter 的最小接口，确保工具发现、调用、结果、错误都能进入 tool_calls。
**Agent:** executor
**References:** 本计划“MCP”“Phase 3”。
**MUST NOT:** 不做插件市场、复杂工具权限、动态沙箱策略。
**Verify:** 用一个示例工具调用流程说明 tool_name、arguments、result/status 如何落库并被 Agent 回复引用。
**Parallel:** 依赖 Step 1；可与 Step 2 并行细化，但最终需对齐 tool_calls 模型。

### Step 4: 设计会话压缩与上下文预算策略
**What:** 确定 Context Manager 的上下文拼装顺序、压缩触发阈值、summary 结构和失败降级策略。
**Agent:** executor
**References:** 本计划“会话压缩/上下文管理设计建议”。
**MUST NOT:** 不做跨会话长期记忆、向量知识库、用户画像。
**Verify:** 给出一个长会话压缩示例，证明旧消息摘要 + 最近消息可继续回答关键事实问题。
**Parallel:** 依赖 Step 2 的 messages/summaries 模型；可在 Step 3 后或并行推进。

### Step 5: 规划固定角色多 Agent 编排
**What:** 将 CoordinatorAgent、WorkerAgent、SummarizerAgent 接入 ACP-lite handoff，定义顺序编排和 run 记录方式。
**Agent:** executor
**References:** 本计划“多 Agent”“Phase 5”。
**MUST NOT:** 不做自动 Agent 团队生成、复杂并行 DAG、自由协商式多 Agent。
**Verify:** 画出或描述 Coordinator -> Worker -> Coordinator 与 Coordinator -> Summarizer 的两条执行链，并说明每一步如何记录 agent_run。
**Parallel:** 依赖 Step 1、Step 2、Step 4。

### Step 6: 串联 MVP 演示路径与验收清单
**What:** 固化一个企业业务操作演示场景，将会话、持久化、MCP、压缩、多 Agent 串成端到端验收。
**Agent:** deep-executor
**References:** 本计划“Phase 6”“MVP 成功标准”。
**MUST NOT:** 不把演示扩展成完整工单系统或企业平台。
**Verify:** 端到端脚本覆盖：创建会话 -> 多轮对话 -> 工具调用 -> 持久化回放 -> 压缩 -> 继续对话 -> 展示执行轨迹。
**Parallel:** 依赖 Step 2-5 完成。

## 后续风险与回退

- **最大风险:** 过早平台化，导致 Agent Core 未闭环。回退方式：只保留单 CoordinatorAgent + mock MCP + rolling summary。
- **第二风险:** 会话压缩损失关键事实。回退方式：提高最近消息保留数量，并在 summary 中强制结构化保存目标、事实、未完成任务。
- **第三风险:** MCP 接入不稳定。回退方式：先用 mock MCP adapter 固化 Tool Runtime 接口，再替换真实 MCP。

## 自检

- 阶段数为 6，符合 3-8 步要求。
- 每个阶段都有可执行验收标准。
- 明确不包含登录/权限，并说明预留方式。
- 未读取仓库、未写业务代码、未联网。
- 计划围绕 Agent Core MVP，避免过度企业平台化。
