# 标准评测题库（Question Banks）

`question-banks/` 保存所有 Agent Runtime 共享的固定、版本化评测套件。测试只加载这些文件，不允许在
运行时让模型生成替代题目。

## 规则

1. 每个业务能力使用一个目录，例如 `nino-data-analysis/`。
2. 保持案例 ID 稳定，使不同提交和语言实现的报告可比较。
3. 每个案例必须记录 `derived_from` 和确定性的预期证据。
4. 只有 Skill、Tool 契约、指标定义或数据库种子真值变化时，才能更新预期事实。
5. 案例、预期或推导来源变化时必须提升套件版本。
6. 标准套件应保持精简，通过 `smoke`（冒烟）和 `standard`（标准）标签控制执行成本。
7. 生成的基准报告不得放入此目录。

## Nino Data 题库

权威套件为 `nino-data-analysis/standard.json`，当前版本 `1.4.0`，包含 14 个基础案例和 23 个实际
测试轮次；其中 6 个 `smoke` 基础案例构成默认低成本回归集。数据库行由 MCP Tool 聚合，不会逐行
复制进 Prompt。

同一个基础案例可以使用 `follow_ups` 声明连续追问。Runner 会为基础案例创建一个 Conversation，并
按顺序复用该 Conversation 执行追问。目前覆盖以下关系：

- `related-history`：只从上一轮已验收答案中解释、换算或改写，不重新查询数据。
- `related-new-data`：与历史有关，但必须查询新数据。
- `related-out-of-scope`：与历史对象有关，但请求的操作不属于当前 Skill 能力。
- `unrelated-new-data`：同一会话中的新业务问题，不能把旧答案当成当前证据。
- `unrelated-out-of-scope`：同一会话中的能力外问题，历史不能扩大 Skill 能力边界。

预期结果可以约束 Run outcome、事件类型和模型阶段。例如历史复用必须出现
`history_reconciliation`（历史归并）阶段，而无关新查询必须禁止该阶段并重新调用业务 Tool。

```bash
cd agent/python
.venv/bin/python evals/live_benchmark.py --list
.venv/bin/python evals/live_benchmark.py --tag smoke
```

只有发布或 Harness、Skill、Tool、模型发生实质变化时才使用 `--tag standard`。
