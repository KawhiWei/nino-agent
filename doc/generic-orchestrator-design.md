# 通用 Orchestrator 设计与扩展规范

> 当前实现：Nino Agent Runtime v0.10.0  
> 目标：主 Agent 不包含具体业务知识；新增业务通过注册 Specialist、Skill 和 MCP 扩展。

## 1. 设计结论

`nino.orchestrator` 是 control plane，负责普通问答、能力发现、任务派发和结果归并。
Specialist、Skill、Reference 和 MCP 属于 task plane，负责具体业务执行。主 Agent 不加载业务
Skill 指令、不读取业务 Reference，也不直接获得业务 MCP Tool schema。

通用编排遵守以下控制面原则：

- 主上下文保存路由和任务状态，不保存具体工作状态。
- 根据候选描述选择最窄匹配能力。
- 使用最小充分结构：能一次派发完成就不构建复杂任务图。
- 子任务返回结构化摘要，主 Agent 负责验收与最终回答。

Nino Runtime 直接加载唯一 primary Agent，因此不额外创建 Orchestrator Skill。Orchestrator 是
Agent 身份和控制面职责，不是参与业务能力竞争的 Skill。

## 2. 两层执行循环

```text
Orchestration Loop
  understand -> answer directly OR dispatch -> reconcile -> dispatch/finish

Specialist Worker Loop
  selected Skill -> reason -> MCP/reference action -> observation -> answer
```

普通知识问答不会为了形式而派发。需要业务数据、外部 Tool、专门规则或独立验证时，主模型调用：

```text
nino_runtime_dispatch_agent(agent_id, skill_id, task, context?)
```

Harness 校验 Agent + Skill 是否来自当前 Capability Catalog。选中后，Worker 才加载完整 Skill、
References 和经过 Skill/Agent 双重白名单过滤的 MCP Tools。

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
  "deliverables": [],
  "findings": [],
  "concerns": []
}
```

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

v0.10.0 已实现直接回答、动态单任务派发、同一 Run 内顺序多派发、结果归并、两种 Worker
Engine、统一 Loop 预算、停止原因和 SQLite checkpoint 事件。当前还没有独立持久化的
TaskGraph/Node/Gate 表，也没有并行节点调度和自动恢复执行。

下一阶段 Loop Engineering 的正确顺序：

1. 增加持久化 `TaskGraph/TaskNode/Gate`，不把低层 Tool Call 误建成图节点。
2. 增加依赖节点、并行安全节点和失败重派策略。
3. 增加写操作的 `awaiting_approval` gate 与幂等执行记录。
4. 在协议消息可安全恢复后增加 checkpoint resume，而不是重新执行已成功副作用。
5. 增加确定性 Evaluator；不要默认加入无限 Reflection。

这些能力属于 Harness/Runtime，不进入业务 Skill，也不改变 MCP 标准协议。
