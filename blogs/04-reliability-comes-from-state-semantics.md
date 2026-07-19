# 第四章：可靠性来自状态语义，而不是多加一个 Agent

> Nino Agent Harness 工程实践（四）
> 主要提交：`8e2a47a`、`46535be`、`175c506`
> 演进区间：`0.15.0 -> 0.16.x`

当 TaskGraph、Planner、Analyst 和 Verifier 都已具备后，一个系统看起来已经足够“多 Agent”。但真正进入运行期，可靠性问题才刚开始：服务重启会不会重复执行？同名节点是否可以复用？Skill 升级后旧结果还算有效吗？验证失败应该重做业务查询，还是只修复验证？追问“那上个月呢”应该复用历史，还是重新规划？

这些问题无法通过再加一个 Reviewer 解决。它们需要精确的状态身份、版本关系和恢复语义。

## 1. 最危险的复用：Node ID 相同就跳过

恢复执行时，Planner 可能再次生成逻辑节点 `summary-query`。最简单的策略是：数据库里存在同名 completed 节点，就直接复用。

但节点名称相同，并不意味着工作相同。以下任一变化都可能让旧结果失效：

- 委派任务从 7 月变成 8 月；
- Skill 版本升级，指标口径改变；
- Acceptance Contract 增加新的负向检查；
- 上游依赖结果发生变化；
- input binding 改为读取不同字段；
- Agent、风险策略或 supersedes 关系变化。

因此 `8e2a47a` 引入两类身份：

```text
logical_node_id：人和 Planner 理解的稳定任务名
physical node id：某个具体执行版本
```

Completed 结果只有在 logical ID 和规范化 fingerprint 都匹配时才能复用。

## 2. Fingerprint 是执行身份，不只是缓存 Key

节点 fingerprint 对以下信息做规范化 SHA-256：Agent、Skill 及版本、任务文本、上下文、Acceptance Contract、input bindings、依赖 fingerprint 和 supersedes 信息。

```text
same logical ID + same fingerprint
  -> 同一个执行身份，可复用 Completed Result

same logical ID + different fingerprint
  -> 新执行版本，创建新的 physical node
```

把 fingerprint 理解成普通缓存 Key 会低估它的作用。它还决定恢复时能否跳过执行，并参与下游身份计算。如果上游 fingerprint 改变，下游 fingerprint 也会改变，旧的下游结果不会被错误复用。

这是一种内容寻址的执行语义：不是“这个名字以前做过”，而是“完全相同的工作定义以前完成过”。

## 3. Completed 必须冻结

系统修订计划时，不应该把历史 completed 节点改回 pending 或 failed。否则审计记录会被当前计划重写，用户无法知道之前到底发生了什么。

Nino Agent 对 completed 节点采取冻结策略：

- fingerprint 相同则直接复用，不产生新 Attempt；
- fingerprint 不同则保留旧 completed 节点，创建新物理节点；
- 新节点 metadata 记录 `supersedes_node_id` lineage；
- 旧结果仍可用于解释历史，但不再代表当前 revision。

历史事实和当前有效状态由 lineage 连接，而不是互相覆盖。

## 4. 自动版本演进与显式 repair 不是一回事

同一 logical node 因 fingerprint 变化产生新版本，是 Runtime 能自动判断的版本演进。显式 repair 则表示 Planner 要用一个新节点替换失败或阻塞的旧工作。

两者有不同规则：

```text
自动版本演进
  同 logical ID + fingerprint 变化
  Runtime 自动建立 lineage

显式 repair
  Planner 提交 supersedes_node_id
  只能替换早期 revision 的 failed/blocked 工作
```

显式 repair 不能替换 running 节点，也不能把被替换节点作为自己的依赖。Orchestrator 校验 proposal，TaskGraphController 再根据持久化状态执行最终检查。

接受 repair 后，旧节点及其尚未完成的下游会进入 `superseded`，Gate 进入 blocked，并记录 `superseded_by_node_id` 或 `invalidated_by_node_id`。已经 completed 的历史节点仍然冻结。

因此失败后的第一步是 reconcile，而不是无条件重试。控制面先判断失败影响了哪些未完成下游、哪些 Completed 仍然可信，再生成新的 revision。对于服务端 Harness，这个判断必须落入持久化状态和事务约束，不能只存在于某次模型对话里。

## 5. Assurance 失败不等于业务工作失败

一个很容易混淆的场景是：Analyst 已经成功执行查询并产生结果，但独立 Verifier 没有通过。

此时业务节点是 completed，Assurance Gate 是 failed。不能把 Analyst 节点当作失败节点直接 supersede，因为它确实完成过一次有证据的工作；失败发生在验证层。

Nino Agent 向 Planner 暴露两个维度：

```text
work_status = completed
assurance_status = failed
supersedable = false
```

Planner 应提出新的独立只读 repair Node，不替代 completed 工作，也不依赖那份未获保证的 claim。即使模型错误提交 supersedes，Orchestrator 也会移除或拒绝非法关系。

这条语义很重要：执行成功、证据存在和结论通过验证，是三个不同状态。把它们压成一个 success/failure，会导致错误的重试和历史改写。

## 6. 服务重启不是从头再跑

NodeAttempt 保存 lease owner、lease expiry、checkpoint 和 attempt number。Runtime 启动后会注册实例并维持心跳，然后查找 owner 失效或 lease 过期的 running Attempt。

恢复流程是：

```text
旧 Attempt -> interrupted / RUNTIME_RESTARTED
对应 Node/Graph/Run -> pending / queued
读取原 trigger message 与 Conversation history
重新运行 Root Orchestrator
根据 logical ID + fingerprint 复用 completed 节点
为未完成工作创建新的 Attempt
```

旧 Attempt 不被覆盖，因此可以看到故障前最后一次授权和 checkpoint。

但这里必须保持克制：当前 checkpoint 不保存完整模型协议状态、隐藏推理或所有 Tool 参数，所以它不是从任意 token 位置恢复。恢复发生在任务节点边界，而不是模型思考中间。

当前实现的恢复对象也是 Runtime 进程内任务配合持久化 lease，而不是独立消息队列中的分布式 Job。它已经比“重启后从聊天历史重跑”更精确，但如果要支撑多副本服务，还需要把调度权和运行通知从进程内字典、Task 与条件等待机制迁移到共享基础设施。

## 7. 为什么只读工具让恢复更容易

当前 Nino Agent 主要处理只读数据分析，这让节点级重试风险相对可控。对于写操作，仅靠 lease 和 fingerprint 远远不够。

一个真正支持写操作的 Harness 还需要：

- idempotency key 与外部系统执行记录；
- approval Gate 和明确的 awaiting approval 状态推进；
- prepare/commit 或等价的副作用协议；
- 对“请求已发送但响应丢失”的不确定状态建模；
- 补偿操作和人工介入边界。

因此代码契约虽然预留 approval 状态，当前实现并没有宣称已经具备完整审批和写操作恢复。这种边界声明比在架构图上画一个 Approval Node 更重要。

## 8. 多轮追问不能一律重新查询

`175c506` 开始强化历史追问。Conversation 已经保存原始消息、Assistant 回答和紧凑上下文，但 Planner 仍需要判断当前输入属于哪一类：

1. 只解释、比较、改写已有回答；
2. 基于已有事实进行简单计算；
3. 请求新的时间范围、对象或外部事实；
4. 与当前 Skill 无关。

只有历史中存在 Assistant 回答时，Planner 才能选择 `nino_runtime_answer_from_history`。随后 Orchestrator 使用无 Tool 模式执行 `history_reconciliation`，只能引用先前已接受回答中的显式事实。

该分支不创建 Specialist 和 Verifier Node。如果历史不足，必须说明需要新查询，不能用模型常识补齐。这样既避免不必要的 MCP 成本，也不把“利用历史”变成“基于记忆猜测”。

## 9. 新查询为什么要建立父图关系

如果追问改变了数据范围，例如从“7 月毛利”变成“那 8 月呢”，它不是对旧回答的纯解释，而是新的业务执行。

新 TaskGraph 可以记录：

```text
parent_graph_id = previous graph
relation_type = conversation_follow_up
```

这表示两次任务属于同一会话演进，但不会直接复用不匹配 fingerprint 的结果。Conversation 负责语义连续性，Graph lineage 负责任务关系，Node fingerprint 负责执行身份，三者各司其职。

## 10. Verifier 失败后的独立修复节点

`175c506` 还处理了保障失败的修复语义。与重跑原 Analyst 相比，独立 repair Node 有几个优势：

- 不修改已经完成的业务证据；
- 修复目标可以明确指向缺失的验证要求；
- 新 Attempt、Tool evidence 和 Gate 可以独立审计；
- Planner 不需要把失败结论塞回原节点继续对话；
- 后续 revision 能明确说明为何增加该工作。

这体现了 TaskGraph 的价值：失败修复不是 Prompt 中的一句“再检查一下”，而是新的、可验收的任务事实。

## 11. CAS、唯一约束和事件序号同样属于可靠性

Agent 可靠性经常只讨论模型和 Prompt，但很多错误实际来自普通并发问题。Nino Agent 在持久化层提供：

- 同一 Conversation 只能有一个 queued/running Run 的唯一约束；
- Event sequence 使用事务原子分配；
- Node claim 在事务内检查状态、依赖和 Gate；
- Node、Gate、Attempt 在一个事务中收口；
- TaskGraph version 使用 compare-and-swap；
- Graph terminal 先于 Run terminal 发布。

模型再聪明，也不能修复两个 Runtime 同时执行同一 Node，或者 Run 已完成但 Gate 尚未提交的问题。Harness 工程最终仍然要服从数据库一致性。

## 12. 可靠性的本质是把“相同”和“完成”定义清楚

这一阶段最值得复用的经验，不是具体的 fingerprint 字段，而是两个问题：

```text
什么情况下，两次工作算同一个工作？
什么情况下，一个结果算真正完成？
```

Nino Agent 的回答是：执行身份由规范化输入、策略、合同和依赖共同决定；完成则由 Node 状态、业务证据、Assurance Gate 和持久化顺序共同决定。

多 Agent 数量不会自动带来这些语义。相反，角色越多，如果状态越模糊，失败和重试就越难解释。可靠性来自确定性控制面，而不是模型之间互相投票。

下一章将进入最后一段演进：如何把这些内部状态变成用户可感知的产品能力，包括最终回答流式传输、一体化 SSE、断线恢复、取消、React 会话客户端和容器化部署。

---

## 代码考据

- `8e2a47a`：规范化 fingerprint、revision lineage、superseded 和 invalidation。
- `46535be`：澄清控制证据、Verifier 条件与最终回答流式事件。
- `175c506`：历史追问、Assurance repair 与容器化。
- `agent/python/src/runtime/task_graph.py`：复用、物理节点、supersede 和恢复投影。
- `agent/python/src/runtime/service.py`：Conversation history、Run 恢复和终态发布。
- `agent/python/tests/test_task_graph.py`：版本变化、依赖变化、后继失效和恢复回归测试。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
