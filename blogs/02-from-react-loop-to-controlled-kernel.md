# 第二章：从 ReAct 循环到受控执行内核

> Nino Agent Harness 工程实践（二）
> 主要提交：`9e67f80`、`be4427b`、`e7286f2`
> 演进区间：`0.10.0 -> 0.13.0`

第一章建立了 Runtime、Harness 和 Infrastructure 的边界，但边界清楚不等于系统可靠。一个 Agent 即使能稳定调用 MCP，也仍然可能选错 Skill、绕过工具直接编答案、重复查询、在缺参时擅自假设，或者最终答案正确但过程完全不可解释。

Nino Agent 接下来的演进没有先追求更多 Agent，而是把三件事连在了一起：执行策略、执行证据和真实评测。核心判断是：企业 Agent 的质量不能只由最后一段文本决定，必须同时检查它经过了哪条路、使用了什么证据、是否遵守预算和权限。

## 1. “答案看起来对”为什么不够

假设用户问：“统计 2026 年 7 月订单毛利，并找出异常订单。”模型可能给出一个格式完整、数字合理的回答，但至少存在四种完全不同的执行过程：

1. 正确调用汇总和异常查询工具，并基于 Observation 回答。
2. 调了工具，但忽略 Tool Result，使用训练知识猜测。
3. 没有调用工具，直接生成了一组数字。
4. 调用了不该使用的工具，碰巧得到正确答案。

如果测试只匹配最终文本，这四条路径可能都通过。但对生产系统而言，它们的可信度完全不同。

因此 `9e67f80` 增加实时评测套件时，没有把评测做成“答案相似度”，而是同时检查：

```text
最终状态 + 路由结果 + 工具证据 + 回答事实 + Loop 安全
```

当前评分结构仍保留这种思想：状态 20 分、路由 20 分、证据 20 分、答案事实 30 分、循环安全 10 分。分值并不重要，重要的是质量模型从“文本质量”扩展成了“执行质量”。

## 2. Skill 路由必须先可拒绝

Agent 平台最容易犯的错误，是把所有请求都送入语义模型，让模型决定该做什么。这看似灵活，实际会让范围外请求也进入业务执行链路。

Nino Agent 把路由拆成两层：

```text
第一层：确定性召回与排除
  intent_keywords / excluded_intent_keywords

第二层：受控语义判断
  只允许 opt-in Skill 进入候选池
```

排除优先于语义 fallback。写订单、修改数据库、新闻、写代码等请求一旦命中排除规则，就不能因为“模型觉得相关”重新进入只读数据分析 Skill。

语义层也不是自由问答。模型只能提交受控决定：进入候选能力、请求澄清、拒绝请求，或者在后续版本中基于已有回答处理追问。模型负责处理语言的不确定性，Harness 负责限制它能产生的状态变化。

这是一条可迁移的设计原则：

> 先用确定性规则缩小模型的权力，再用模型解决规则难以覆盖的语义问题。

## 3. 缺参澄清是一种终态，不是错误提示

用户说“帮我看一下订单情况”，缺少日期、范围、订单号和分组维度。一个不受控 Agent 往往会自行补全参数，或者先执行一次宽泛查询再追问。

Nino Agent 将澄清建模为专用内部 Action。模型需要显式调用 `nino_runtime_request_clarification`，Harness 校验问题长度和上下文，然后以结构化 outcome 结束当前 Run。

这和在最终文本中写一句“请提供日期”有本质区别：

- 客户端可以识别当前结果是 clarification；
- 评测可以要求某类请求必须澄清；
- TaskGraph 可以把它作为控制证据，而不是业务证据；
- 后续输入可以在同一个 Conversation 中继续。

`46535be` 后来进一步明确：澄清可以满足当前控制流程的终态要求，但不能满足业务事实的 Evidence Gate。控制证据和业务证据必须分开。

## 4. 工具可见性本身就是权限

很多 Agent 系统只在工具执行前检查权限，却把全部 Tool schema 都交给模型。这仍然会增加误选工具、Prompt 注入和跨业务污染的风险。

Nino Agent 在 Worker 调用模型之前就裁剪工具目录：

```text
有效工具
  = MCP discovery catalog
  ∩ Skill.allowed_tools
  ∩ Agent role policy
```

如果 Skill 声明的必需工具没有发现，Worker 在模型调用前失败，而不是让模型退化成无工具回答。对于事实型数据任务，这种失败比“尽力回答”更诚实。

内部 Action 也与业务 Tool 分开：加载 Reference、请求澄清、提交验证裁决、委派 Agent，都属于 Runtime 控制动作。它们可以推进流程，但不能自动成为业务事实证据。

## 5. Evidence Gate：没有 Observation 就没有事实

`be4427b` 把执行内核推进到任务级 Harness，其中一个关键变化是 Evidence Gate。对于事实性 Specialist 节点，至少要存在一次：

```text
成功 + 非 Reference + 非 Runtime 内部 Action + 经批准的业务 Tool Observation
```

否则节点返回 `EVIDENCE_REQUIRED`，不能因为模型给出了一段流畅文本就被接受。

Evidence Gate 不证明答案一定正确，它只证明回答至少经过了规定的数据通道。这个边界必须说清：

- Harness 可以确定性地证明“工具调用发生且成功”；
- Tool Result 是否对应真实世界，取决于 MCP 与数据源；
- 模型是否正确理解 Tool Result，还需要 Acceptance Contract 和 Verifier；
- 最终业务正确性不是单一 Gate 能形式化证明的。

工程上的价值在于，把“可信”拆成多个可检查条件，而不是一次性相信模型。

## 6. Acceptance Contract 让完成定义一致

如果 Analyst、Verifier 和 Orchestrator 对“完成”的理解不同，验证就会变成新的自由推理。任务级内核引入 `AcceptanceContract`，把验收要求作为节点的一部分：

```json
{
  "target_outcome": "统计 2026 年 7 月毛利",
  "positive_checks": ["结果覆盖指定月份"],
  "negative_checks": ["不得把缺失数据解释为 0"],
  "evidence_requirements": ["至少一次成功业务 Tool Observation"],
  "pass_label": "business_result_verified"
}
```

同一份合同会传给 Worker、Verifier 并持久化在 TaskNode 上。它解决的不是 Prompt 复用问题，而是完成定义的一致性问题：执行者知道要交付什么，验证者知道要检查什么，控制面知道何时允许节点通过。

合同缺失时 Harness 可以生成保守默认值，但高价值任务应该提供任务专属的正向检查、负向检查和证据要求。

## 7. Verifier 必须独立取证

多加一个“审核 Agent”并不会自然提高可靠性。如果 Verifier 只阅读 Analyst 的答案，然后说“看起来合理”，它只是第二次语言生成。

Nino Agent 对 Verifier 做了更严格的约束：

1. 使用 fresh context，不继承 Analyst 的隐藏推理。
2. 接收原任务、Acceptance Contract 和 Analyst claim。
3. 不把 Analyst 文本当事实证据。
4. 重新调用最小必要的只读 Tool。
5. 通过内部 Action 返回结构化 verdict。

```json
{
  "verdict": "passed",
  "evidence_level": "proved",
  "checked_requirements": ["金额和订单范围与 Tool 结果一致"],
  "failed_requirements": [],
  "concerns": []
}
```

只有 verdict 为 passed、evidence level 为 proved 且存在独立 Tool evidence，验证 Gate 才通过。这里的“proved”表示满足当前 Harness 证据协议，不等同于数学意义上的形式化证明。

## 8. Event 是执行证据，Graph 是控制状态

随着 TaskGraph 加入，系统同时拥有两套看似相似的数据：Run Events 和 Graph 状态。它们不能互相替代。

```text
Event：发生过什么
Graph：当前可以做什么
```

`model_started`、`tool_completed`、`agent_completed` 适合流式展示、审计和断线重放；Node、Gate、Attempt 适合依赖调度、执行授权和恢复。TaskGraphController 消费事件并投影状态，但 Graph Truth 必须落入独立的事务模型。

Nino 不把 Graph 管理暴露成模型可以直接操作的 Tool。Harness 只发出规划和执行事件；`AgentRuntimeService` 先持久化 Event，再由 `TaskGraphController` 事务化投影 TaskGraph、Node、Gate 和 Attempt。业务 MCP 属于外部证据面，Graph Truth 属于服务端控制面。

这也是为什么 Run 不能先标记 completed，再异步补 Graph。Nino Agent 的顺序是：

```text
Node/Gate/Graph terminal 持久化
  -> 发布 Run terminal
  -> 写入 Assistant message
```

客户端看到完成时，数据库中已经存在一条能够解释“为什么完成”的任务状态链。

## 9. 实时评测为什么必须跑真实链路

单元测试可以验证权限交集、预算计算和事件投影，但无法证明某个真实模型会遵循 Tool Calling 协议，也无法发现 Prompt、模型版本、MCP 数据和题库事实之间的偏差。

实时 Benchmark Runner 通过公开 API 创建 Conversation、提交问题、等待 Run、读取 Events，然后按题库契约评分。题库不仅定义答案事实，还定义：

- 期望状态和 Skill；
- 是否应进入业务链路；
- 必须或禁止调用的工具；
- 必须读取的 Reference；
- 必须或禁止出现的模型阶段与事件；
- 最大模型调用次数；
- 多轮 follow-up 的预期 outcome。

这让一次回归能够回答：“数字对不对”“有没有走错路”“有没有越权”“是否浪费调用”“追问是否错误触发新查询”。

`46535be` 修复四道未满分题的过程也说明了评测的价值：问题并不只在 Prompt，还涉及路由、澄清终态、异常分析预算、MCP 汇总字段和题库契约。真实评测迫使系统按端到端事实收敛，而不是只在局部测试中自洽。

## 10. 受控内核的工程判断

从 `9e67f80` 到 `be4427b`，Nino Agent 完成了一次重要转变：ReAct 不再只是一个循环，而成为受策略、证据、合同、Gate、持久化和评测共同约束的执行内核。

可以把这套思路压缩成五句话：

1. 模型可以提出 Action，但不能直接产生副作用。
2. 成功 Tool Observation 是事实声明的最低门槛。
3. 执行者和验证者共享验收合同，但不共享事实假设。
4. 最终文本、执行路径和资源预算必须一起评测。
5. 事件负责解释历史，Graph 负责决定未来。

下一章进入这次演进中最核心的结构：TaskGraph。重点不是怎样让模型生成 DAG，而是为什么模型只能提交候选计划，为什么 Tool Call 不能直接成为任务节点，以及 Planner、Orchestrator 和 Repository 应如何分配控制权。

---

## 代码考据

- `9e67f80`：强化 Skill 路由、证据策略并加入实时评测套件。
- `be4427b`：加入持久化 TaskGraph、Gate、Attempt、Evaluator 和 Acceptance Contract。
- `e7286f2`：将分散设计统一为任务级 Harness 文档。
- `agent/python/evals/live_benchmark.py`：真实 API 评测和五维评分。
- `agent/python/src/harness/validation.py`：Node Result 与 Evaluator Verdict 归一化。
- `agent/python/src/runtime/task_graph.py`：事件到 Graph Truth 的持久化投影。
- `agent/shared/contracts/`：TaskGraph、Node Result、Evaluator 和题库契约。

## 项目源码

[KawhiWei/nino-agent](https://github.com/KawhiWei/nino-agent)
