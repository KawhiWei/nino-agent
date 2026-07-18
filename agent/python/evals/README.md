# Live Agent Benchmark

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
- Orchestrator 是否正确拒绝或委派，以及 Skill 是否正确：20 分。
- MCP Tool 与 Reference 是否符合 Skill：20 分。
- 最终答案是否包含数据库真值或预期边界说明：30 分。
- Loop 是否无 Tool 错误且始终处于 step/action/timeout 预算内：10 分。

当前标准题库刻意控制为 8 题，覆盖范围拒绝、缺参澄清、只读边界、单订单、多资源、月度汇总、
最低毛利列表和全年渠道分析。25,040 个订单只由 MCP 做数据库聚合，不会逐行进入模型上下文；修改
seed 后必须同步修改聚合真值并提升题库版本。

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
