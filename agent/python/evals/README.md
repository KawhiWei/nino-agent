# 真实 Agent 基准测试（Live Agent Benchmark）

该 Runner 读取 Skill 自己声明的共享标准题库，通过 Runtime REST API 调用真实模型，并使用可重放
事件和数据库已知真值评分。Python 文件不内置业务题目，也不读取、保存或评估隐藏思维过程。

默认题库：

```text
agent/shared/question-banks/nino-data-analysis/standard.json
```

每题必须包含 `derived_from`，说明它来自哪条 Skill 规则、Reference、Tool 契约或数据库 Golden
Case。Runner 会在调用模型前校验题库中的 Tool 和 Reference 属于目标 Skill。

题目不会在测试运行时生成。业务规则、Tool 契约或种子真值变化时，人工更新固定题目并提升题库
`version`；普通代码改动只重复运行同一版本题库。

## 评分

每题 100 分：

- Run 正常完成：20 分。
- Planner 是否提交合法候选、Orchestrator 是否正确接受或拒绝，以及 Skill 是否正确：20 分。
- MCP Tool 与 Reference 是否符合 Skill：20 分。
- 最终答案是否包含数据库真值或预期边界说明：30 分。
- Loop 是否无 Tool 错误且始终处于 step/action/timeout 预算内：10 分。

当前标准题库包含 14 个基础案例、23 个实际测试轮次，覆盖范围拒绝、语义能力召回、缺参澄清、
只读边界、创建订单与退款操作拒绝、单订单、供应商退款回收、负毛利、不存在订单、多资源、月度汇总、
最低毛利列表和全年渠道分析。25,040 个
订单只由 MCP 做数据库聚合，不会逐行进入模型上下文；修改 seed 后必须同步修改聚合真值并提升
题库版本。

## 多轮追问

基础案例的 `follow_ups` 会在同一个 Conversation 中依次执行。报告中的 `case_count` 是基础案例数，
`turn_count` 是包含追问在内的实际轮次数。订单与澄清案例验证以下关键行为：

1. 有关追问只改写已确认数据：Planner 选择历史回答控制动作，Runtime 进入
   `history_reconciliation`（历史归并）阶段，不调用业务 Tool。
2. 无关但仍属于业务的新问题：重新提交工作节点并查询新订单，禁止使用历史归并代替新证据。
3. 无关且超出能力范围的问题：返回 `out_of_scope`（超出能力范围），旧会话历史不能扩大 Skill 边界。
4. 缺少订单号时先澄清，用户补充参数后继续真实查询，随后才允许使用已接受结果回答历史追问。
5. 支持的查询之后要求执行退款仍必须拒绝；拒绝之后的新查询仍能重新进入受控业务执行链路。

`--case` 选择的是基础案例，因此会同时执行该案例声明的全部追问；`--list` 会用
`基础案例 ID/追问 ID` 展示每一个追问。

月度亏损订单总数来自 `nino_data_query_summary.data.totals.negativeMarginOrderCount`；异常列表是
有上限的 Top N 结果，不应为了总数评分强制调用，也不能使用返回行数代替总数。

## 执行

确保 Runtime 以 `live` 模式运行，然后执行：

```bash
cd agent/python
.venv/bin/python evals/live_benchmark.py --tag smoke
```

发布前执行全部标准题：

```bash
.venv/bin/python evals/live_benchmark.py \
  --tag standard \
  --output ../../nino-agent-storage/live-benchmark.json
```

查看题目清单但不调用模型：

```bash
.venv/bin/python evals/live_benchmark.py --list
```

只执行指定题目并保存完整报告：

```bash
.venv/bin/python evals/live_benchmark.py \
  --case order-detail-margin-001 \
  --output ../../nino-agent-storage/live-benchmark.json
```

基准报告会保留回答正文和 `run_id`，可以通过 Run Event API 复查每个扣分点。

题库契约中的 `expected.dispatch` 是历史稳定字段，含义是“请求是否应进入受控业务执行链路”，不是
“Orchestrator 自己生成计划”。在 `0.14.0` 中，期望为 `true` 时应观察到 Planner proposal、
Orchestrator 接受的 Graph revision，以及后续 Analyst/Verifier 执行。
