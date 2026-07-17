# 通用 Orchestrator 设计与扩展规范

> 当前实现：Nino Agent Runtime v0.13.0
> 目标：主 Agent 不包含具体业务知识；新增业务通过注册 Specialist、Skill 和 MCP 扩展。

## 1. 设计结论

`nino.orchestrator` 是 strict-scope control plane，负责 Skill 范围门禁、能力发现、任务派发和结果归并。
Specialist、Skill、Reference 和 MCP 属于 task plane，负责具体业务执行。主 Agent 不加载业务
Skill 指令、不读取业务 Reference，也不直接获得业务 MCP Tool schema。

通用编排遵守以下控制面原则：

- 主上下文保存路由和任务状态，不保存具体工作状态。
- 当前请求未命中关键词时，只向模型暴露显式 opt-in 的语义候选；确定性排除规则仍优先。
- 根据候选描述选择最窄匹配能力。
- 命中 Skill 后必须至少完成一次合法 dispatch，禁止主模型直接自由回答。
- 使用最小充分结构：能一次派发完成就不构建复杂任务图。
- 子任务返回结构化摘要，主 Agent 负责验收与最终回答。

Nino Runtime 直接加载唯一 primary Agent，因此不额外创建 Orchestrator Skill。Orchestrator 是
Agent 身份和控制面职责，不是参与业务能力竞争的 Skill。

## 2. 两层执行循环

```text
Orchestration Loop
  scope gate -> reject OR dispatch -> reconcile -> dispatch/finish

Specialist Worker Loop
  selected Skill -> reason -> MCP/reference action -> observation -> evidence gate -> answer
```

Harness 先使用注册 Skill 的 `excluded_intent_keywords` 排除禁用操作，再用 `intent_keywords`
进行确定性白名单匹配。未命中时，只有声明 `routing.semantic_fallback=true` 的 Skill 进入受控语义
候选目录；主模型必须结构化 dispatch、请求澄清或拒绝，不能自由回答。命中时主模型必须调用：

```text
nino_runtime_dispatch_agent(
  agent_id, skill_id, task, context?, depends_on?, input_bindings?, acceptance_contract?
)
```

Harness 校验 Agent + Skill 是否来自当前匹配后的 Capability Catalog。选中后，Worker 才加载完整
Skill、References 和经过 Skill/Agent 双重白名单过滤的 MCP Tools。Worker 没有成功 Tool
Observation 时不能输出自由文本；缺参必须调用受 Harness 校验的结构化
`nino_runtime_request_clarification` Action。

## 3. 动态 Capability Catalog

目录由 `AgentRegistry` 和 `SkillRegistry` 在运行时生成。向主模型暴露的仅是：

```json
{
  "agent_id": "nino-data.analyst",
  "agent_description": "...",
  "agent_capabilities": ["data-analysis", "order-query"],
  "skill_id": "nino-data.analysis",
  "skill_description": "...",
  "skill_capabilities": ["order-query", "grouped-statistics"],
  "risk_level": "read-only"
}
```

`discover_delegates=true` 只表示主 Agent 可以发现已注册 Specialist，不等于任意执行。有效候选必须
同时满足：Agent 是 Specialist、Skill 存在、Skill 位于该 Agent 的 `allowed_skills`、执行时 MCP Tool
位于 Skill 与 Agent 的 `allowed_tools` 交集。

## 4. Dispatch Result

子 Agent 的结果归一化为：

```json
{
  "kind": "dispatch_result",
  "status": "completed",
  "agent_id": "nino-data.analyst",
  "skill_id": "nino-data.analysis",
  "child_run_id": "...",
  "summary": "...",
  "outputs": {},
  "findings": [],
  "concerns": [],
  "recommended_next": []
}
```

`depends_on` 定义控制依赖；`input_bindings` 从已完成上游 Node 的结构化 Result 中选择字段并注入
下游 fresh context。Acceptance Contract 在执行前确定，并由 Worker、Evaluator 和持久化 TaskGraph
共同使用。Workflow 和 Assurance Policy 是 Skill manifest 中独立的控制面元数据，不进入 Tool 层。

父 Run 会归并子 Worker 的 `skill_selected/model_started/tool_started/tool_completed/reference_loaded`
事件，并增加 `parent_step/child_run_id/agent_id/skill_id`，因此 REST/SSE 客户端仍能验证真实 MCP 调用。

## 5. 新增业务能力

增加新业务不修改 `nino.orchestrator`：

1. 在 `agent/shared/skills/<skill>/` 增加 `skill.json`、`SKILL.md` 和可选 References。
2. 在 `agent/shared/agents/<specialist>/` 增加 Specialist 定义。
3. Specialist 的 `allowed_skills` 指向新 Skill，`allowed_tools` 使用 MCP 的稳定 Tool 名称。
4. 在 Compose/环境变量中注册对应 MCP Server。
5. 增加 capability routing、权限拒绝、Worker Tool 和端到端事件测试。

Runtime 重启后，Capability Catalog 自动包含新组合。主 Agent 指令不应追加具体业务名称。

## 6. 当前边界与下一阶段

当前已实现严格范围拒绝、计划型批量派发、结果归并、两种 Worker Engine、统一 Loop 预算、持久化
TaskGraph/Node/Gate/Attempt、独立 Verifier Gate、进程重启恢复、依赖校验、Ready Node 波次调度、
安全并行和失败后的 Graph revision reconcile。

下一阶段 Loop Engineering 的正确顺序：

1. 让恢复逻辑直接加载持久化计划，从未完成 Ready Node 继续，而不是重跑整个 Root Node。
2. 增加写操作的 `awaiting_approval` gate、幂等执行记录和 Action ledger。
3. 增加身份、租户、RBAC 与数据权限上下文。
4. 将当前 SQLite Node claim/lease 和进程内全局并发配额扩展为远程共享存储上的跨主机协调。
5. 扩展 Reviewer/Critic 等风险型 Evaluator；不要默认加入无限 Reflection。

这些能力属于 Harness/Runtime，不进入业务 Skill，也不改变 MCP 标准协议。
