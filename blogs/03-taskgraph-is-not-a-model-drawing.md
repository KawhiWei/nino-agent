# 第三章：TaskGraph 不是模型画出来的一张图

> Nino Agent Harness 工程实践（三）
> 主要提交：`be4427b`、`facbc9e`、`8e2a47a`
> 演进区间：`0.13.0 -> 0.15.0`

把复杂任务拆成 DAG，已经成为 Agent 架构中的常见做法。但“让模型输出一张图”和“实现任务级 Harness”不是同一件事。前者解决表达，后者必须解决控制权、持久化、调度、验收、并发、恢复和版本修订。

Nino Agent 引入 TaskGraph 后，很快又在 `facbc9e` 把 Planner 从 Orchestrator 中拆出。这次拆分揭示了整个设计的核心：Planner 只负责提出候选计划，Orchestrator 才是唯一控制面，Repository 的原子 claim 才是最终执行授权。

Runtime 会为每个 Run 建立一个最小 Root Graph，使所有请求都有统一的状态、终态和恢复入口。普通回答、范围外拒绝或澄清可以只在 Root 收口，不创建业务子图；只有受控路由命中业务能力后，Planner proposal 才会扩展出 Specialist、Verification、依赖和 Gate。统一 Graph Truth 不代表所有请求都要支付完整多 Agent 流程成本。

## 1. 为什么 ReAct Loop 不足以表达复杂任务

单个 ReAct Loop 擅长局部闭环：模型决策、调用工具、读取结果、继续决策。但当一个任务包含分析、交叉验证、并行查询和失败修复时，仅靠消息历史会出现几个问题：

- 已完成工作只存在于上下文中，难以稳定复用；
- 节点之间的依赖和数据传递没有显式契约；
- 服务重启后无法判断哪些动作已经完成；
- 验证步骤容易被模型当作普通 Tool Call 跳过；
- 并行执行和失败传播依赖临时控制代码；
- 前端只能看到“第几轮”，看不到宏观任务结构。

TaskGraph 的作用不是替代 Worker ReAct，而是在更高一层表达任务真相：哪些语义工作需要完成，它们之间有什么依赖，每个结果通过了什么 Gate。

## 2. Tool Call 为什么不是 TaskNode

最直接的建图方式是把每个模型 Tool Call 变成节点。但这会把图退化成模型内部轨迹的持久化副本。

在 Nino Agent 中，TaskNode 是有独立结果和验收意义的语义工作边界，例如：

```text
统计指定月份毛利
识别异常订单
独立验证统计口径
汇总已验证结论
```

而一次 `nino_data_query_summary` 调用只是某个节点内部的 Action。一个节点可以调用多个工具，也可能重试某个工具；这些动作应该记录在 Event 和 Attempt checkpoint 中，而不是把业务图切成低层协议步骤。

区分标准很简单：

> 如果这个单元不能独立说明“交付了什么、如何验收”，它通常不是任务节点。

这使 TaskGraph 对模型 SDK、MCP 工具粒度和 Worker 实现保持稳定。

## 3. Planner、Orchestrator 和 Repository 的三权分离

`facbc9e` 增加 `nino.planner` 后，四个角色的边界更清楚：

| 角色 | 拥有什么权力 | 没有什么权力 |
|---|---|---|
| Planner | 提交候选节点、依赖、输入绑定和验收合同 | 持久化、调度、调用业务 MCP、生成最终回答 |
| Orchestrator | 校验 proposal、接受 revision、调度、Gate、归并 | 绕过 Repository 直接授权执行 |
| Worker | 在选定 Skill 和合同内完成节点 | 修改 Graph、决定全局完成状态 |
| Repository | 原子 claim、lease、CAS 和事务提交 | 语义规划与业务判断 |

可以把控制链写成：

```text
Planner proposal
  -> Orchestrator deterministic validation
  -> persisted Graph revision
  -> Scheduler computes ready wave
  -> Repository atomic claim
  -> Worker execution
```

任何一步都不能省略。模型输出的 JSON 只是非可信输入；Scheduler 算出 ready 也只是调度建议；只有数据库成功 claim Node 并创建 Attempt，执行者才真正获得授权。

在服务端部署语境里，这条链路还有一层所有权：Orchestrator 不直接写 Repository。它通过 Event callback 把接受的 proposal 和节点状态交回 Runtime，Runtime 先持久化事件，再投影 Graph Truth。这样模型控制面、执行生命周期和数据库事务没有揉成同一个对象。

## 4. Planner 为什么不是另一个自治 Loop

Nino Planner 每个 revision 只做一次 ModelTurn。它接收用户目标、会话历史、候选能力元数据和紧凑节点结果，然后通过受限 Action 提交一个或多个候选节点。

它不拥有自己的长期 ReAct Loop，原因有三点：

1. Planner 不需要业务 Tool，没有理由在规划阶段持续探索外部世界。
2. 多轮自治规划会形成第二套控制循环，与 Orchestrator 争夺预算和终态。
3. 一次 proposal、一次确定性校验更容易形成清晰的 revision 边界。

Planner manifest 仍保留通用预算字段，但实际调用计入 Orchestration Loop 的 step、action 和 timeout。这样系统始终只有一个 Root 控制循环。

## 5. 模型只能看到能力元数据

Planner 不加载业务 Skill 正文、Reference 内容和 MCP Tool schema。它看到的是精简 Capability Catalog：

```json
{
  "agent_id": "nino.analyst",
  "agent_capabilities": ["data-analysis", "order-query"],
  "skill_id": "nino-data.analysis",
  "skill_capabilities": ["order-query", "grouped-statistics"],
  "risk_level": "read-only",
  "workflow_execution_shape": "adaptive",
  "assurance_mode": "strict_verify"
}
```

Planner 据此选择“谁用什么能力完成哪项工作”，但不知道工具参数细节。具体 Tool schema 只在 Worker 被选中后按权限加载。

这降低了规划上下文体积，也防止 Planner 从“任务拆分器”滑向“偷偷执行任务的超级 Agent”。

## 6. TaskGraph 的五个稳定对象

任务级内核没有只存一段 graph JSON，而是建立了五类稳定对象。

### TaskGraph

表示一个 Run 的宏观任务，包括状态、版本、用户意图、父图关系和 revision metadata。

### TaskNode

表示语义工作边界，保存 owner、依赖、Acceptance Contract、结构化结果、错误和时间。

### TaskGate

表示节点能否被接受的检查点，例如 evidence、independent verification 和 root acceptance。Gate 不是执行者。

### NodeAttempt

表示一次执行授权，保存 attempt number、lease owner、lease expiry、checkpoint 和错误。重试不会覆盖旧 Attempt。

### AcceptanceContract

表示节点可以诚实声称什么，是 Worker、Verifier 和 Orchestrator 共享的完成定义。

这五个对象分别回答：“整体是什么”“要做什么”“是否通过”“谁执行过”“按什么标准验收”。把它们压进单个 Node status，最终会丢掉恢复和审计所需的信息。

## 7. DAG 验证：不要相信模型提交的依赖

Planner 一次 ModelTurn 可以提交多个 Node Action，它们共同组成候选 revision。Orchestrator 和 Scheduler 在接受前检查：

- 逻辑 Node ID 格式和 revision 内唯一性；
- Agent/Skill pair 是否来自 Capability Catalog；
- Task 是否非空；
- 依赖是否来自本 revision 或已知历史；
- 依赖图是否成环；
- input binding 的 source 与 selector 是否有效；
- repair 的 supersedes 关系是否合法。

只有校验后的 proposal 才会成为 `graph_planned` 或 `graph_reconciled`，并持久化为 Graph Truth。

这体现了 Harness 对模型输出的一贯态度：模型负责提出高层意图，确定性代码负责把意图变成合法状态变化。

## 8. 控制依赖和数据依赖必须分开

`depends_on` 只能说明“B 必须在 A 之后执行”，但不能说明 B 应该读取 A 的哪部分结果。如果直接把整个上游上下文传下去，节点隔离会再次失效。

Nino Agent 使用 `input_bindings` 显式声明数据依赖：

```json
{
  "name": "upstream_metrics",
  "source_node_id": "summary-query",
  "selector": "outputs"
}
```

允许选择 `summary`、`outputs`、`findings`、`evidence`、`concerns` 或 `recommended_next`。Binding source 必须同时出现在控制依赖中。

因此下游得到的是裁剪后的结构化结果，而不是上游 Agent 的完整消息、隐藏推理或原始 Tool dump。这既减小上下文，也降低数据越界传播。

## 9. Ready Wave 与原子 claim

Scheduler 计算当前 ready wave：依赖全部完成且必要 Gate 已通过的节点进入 Ready；互不依赖的节点可通过 `asyncio.gather` 并行，并受 `NINO_GRAPH_MAX_PARALLEL_NODES` 限制。

但 Scheduler 不能直接把节点标记为 running。Repository 必须在事务中再次检查：

- Node 仍然 pending；
- 依赖和 required Gate 仍然满足；
- 没有其他 Runtime 已持有有效 lease；
- Attempt 编号可以安全递增。

成功后才创建 running Attempt。这个设计为多 Runtime、服务重启和未来分布式 Worker 留出了正确边界。内存中的“我准备执行”不等于数据库中的“我获得授权”。

但“留出边界”不等于当前已经是分布式执行平台。现有任务仍由单个 Python Runtime 内的 `asyncio` Task 和 Semaphore 执行，Repository 默认是 SQLite；lease、runtime heartbeat、数据库唯一约束和 CAS 主要提供重启恢复及并发正确性。真正的多实例部署仍需要共享生产数据库、外部任务队列或 Worker 协议、跨实例通知与完整故障转移验证。

## 10. Planner 不负责展开强制验证节点

Skill 可以声明 `assurance.required_evaluators`。Planner 提交业务 Specialist 节点后，Orchestrator 根据注册策略自动展开 Verification Node 和 Gate。

这样做比要求 Planner 每次记得添加 Verifier 更可靠：

- Assurance 是平台策略，不是模型建议；
- Planner 无法通过漏画节点绕过验证；
- Verifier 的依赖、合同和 owner 可以确定性生成；
- Skill 升级 Assurance 策略后不必改 Planner Prompt。

同样，最终归并由 Orchestrator 完成，而且不再调用业务 Tool。它只能使用已经通过 Gate 的结构化节点结果。

## 11. Graph revision 不是覆盖旧计划

复杂任务第一次计划可能失败，或者执行结果暴露了新的工作。Nino Agent 没有就地改写原图，而是追加 revision：

```text
revision 1: 初始计划
revision 2: 针对失败或缺口增加 repair work
revision 3: 必要时继续 reconcile
```

每个 revision 保存单调递增编号、revision ID、parent revision ID、原因、接受的物理 Node ID 和 fingerprint 映射。历史不会被覆盖。

这让调试可以回答：“模型最初计划了什么”“为什么增加修复节点”“哪个版本替代了哪个节点”，也为下一章的安全复用建立了 lineage。

## 12. TaskGraph 带来的真正变化

引入 TaskGraph 后，Harness 的核心不再是一个更复杂的 Prompt，而是一台显式状态机：

```text
proposal -> validation -> persistence -> scheduling -> claim
-> execution -> evidence -> evaluation -> reconciliation -> completion
```

模型仍然重要，它负责理解意图、提出任务边界和生成局部结果。但它不再独占系统状态。计划、执行授权、证据和完成状态分别落入可验证的工程对象。

这也是 TaskGraph 与“模型画 DAG”最本质的区别：图不是模型输出的展示物，而是 Runtime 用来约束未来动作的持久化事实。

下一章继续讨论图进入真实运行后最棘手的问题：相同节点能否复用，Skill 或依赖变化时如何处理，服务重启后怎样避免重复副作用，以及多轮追问为什么不能简单复制上一张图。

---

## 代码考据

- `be4427b`：持久化 TaskGraph、TaskNode、Gate、Attempt 与依赖调度。
- `facbc9e`：拆出 `nino.planner`，统一业务中立四角色架构。
- `8e2a47a`：加入 revision lineage、fingerprint 和 superseded 语义。
- `agent/python/src/harness/planning.py`：Planner 单轮结构化提案。
- `agent/python/src/harness/scheduler.py`：DAG lint 和 ready wave。
- `agent/python/src/framework/task_graph.py`：图领域对象与稳定状态。
- `agent/python/src/runtime/task_graph.py`：Graph Truth 投影、claim 和 revision 持久化。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
