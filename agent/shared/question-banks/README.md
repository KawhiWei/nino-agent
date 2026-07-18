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

权威套件为 `nino-data-analysis/standard.json`，当前版本 `1.2.0`，包含 8 个代表性案例；其中 5 个
`smoke` 案例构成默认低成本回归集。数据库行由 MCP Tool 聚合，不会逐行复制进 Prompt。

```bash
cd agent/python
.venv/bin/python evals/live_benchmark.py --list
.venv/bin/python evals/live_benchmark.py --tag smoke
```

只有发布或 Harness、Skill、Tool、模型发生实质变化时才使用 `--tag standard`。
