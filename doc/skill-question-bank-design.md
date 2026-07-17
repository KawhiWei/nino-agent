# Skill 标准题库蒸馏与评测设计

## 1. 结论

标准题库不能在每次测试时让模型随机出题。随机题无法稳定复现，容易把题目质量、模型波动和
Runtime 缺陷混在一起，也无法让 Python、Node.js、.NET Runtime 得到可比较分数。

Nino Agent 使用版本化的“契约蒸馏”方式：从 Skill 已声明的范围、执行规则、Reference 和确定性
数据 Oracle 中提取题目，再人工确认预期。模型可以辅助生成候选措辞，但不能直接决定标准答案。

## 2. 文件归属

```text
agent/shared/
├── contracts/
│   └── evaluation-suite.schema.json
└── skills/nino-data-analysis/
    ├── skill.json
    ├── SKILL.md
    ├── references/
    └── evals/
        └── standard.json
```

题库属于 Skill，而不是 Python Runtime。`skill.json.evaluation_suites` 声明题库路径，所有语言实现
读取同一个 JSON。语言专属目录只保存 Runner Adapter 和报告，不复制题目。

## 3. 四层蒸馏来源

### 3.1 范围契约

从 `skill.json` 提取：

- `intent_keywords` 生成合法范围题。
- `excluded_intent_keywords` 生成越界、写入和混合提示词题。
- `risk_level` 生成权限边界题。
- `allowed_tools` 生成 Tool 白名单和禁止调用断言。
- Loop 配置生成 step/action/timeout 预算断言。

### 3.2 执行规则

从 `SKILL.md` 提取：

- Tool Selection 生成订单、汇总和异常路由题。
- Reference Routing 生成必需 Reference 断言。
- 缺参规则生成结构化 clarification 题。
- Answer Shape 生成币种、日期范围、限制说明等输出断言。

### 3.3 业务定义

从 `references/*.md` 提取：

- 指标公式和日期区间。
- 退款成功状态、供应商回收和毛利口径。
- 异常 reason codes。
- 报表结构与证据要求。

Reference 负责定义“如何解释”，但不应单独提供易变化的数据答案。

### 3.4 确定性 Oracle

从 migration、seed、数据库 assertions 或专用验证查询提取精确值。例如：

```text
DEMO-202607-001 -> 225 - 165 - 0 = 60
DEMO-202607-032 -> 500 - 450 - 500 = -450
2026-07 summary -> 38 orders, margin 1470 CNY
```

标准答案不能由另一个模型评价。金额、数量、排序和状态必须来自确定性 Tool、SQL assertion 或
固定 fixture。

## 4. 当前题库矩阵

当前 `nino-data.analysis.standard` v1.0.1 共 12 题：

| 类型 | 数量 | 覆盖 |
|---|---:|---|
| Scope | 2 | 普通问题拒绝、业务词混合提示注入 |
| Clarification | 1 | 缺订单号时结构化追问 |
| Boundary | 1 | 创建订单和数据库写入拒绝 |
| Order Query | 4 | 普通盈利、部分退款、多资源、不存在订单 |
| Anomaly | 3 | 客户退供应商未退、供应商拒退、最低毛利五单 |
| Aggregate | 1 | 月度总计、退款、毛利和亏损数量 |

其中 7 题带 `smoke` 标签，适合日常快速回归；全部 12 题带 `standard` 标签，用于发布验收。

## 5. 每题必须声明

- `id/category/tags/prompt`：稳定身份和执行入口。
- `derived_from`：对应的 Skill、Reference、Tool 或数据库 Oracle。
- `expected.status/skill_id/dispatch`：路由与终态。
- `required_tools/forbidden_tools`：Action 边界。
- `required_references`：按需知识加载。
- `max_model_calls`：模型前拒绝等确定性路径约束。
- `answer_facts`：每组是可接受等价文本，组与组之间必须全部命中。

题库不保存隐藏思维链，也不按 `reasoning_content` 评分。ReAct 是否成立通过 Tool、Observation、
checkpoint 和最终证据验证。

## 6. 评分

每题 100 分：

| 维度 | 分数 | 判断依据 |
|---|---:|---|
| Status | 20 | Run 是否达到预期终态 |
| Routing | 20 | 是否拒绝/dispatch 到正确 Skill |
| Evidence | 20 | 必需/禁止 Tool 与必需 Reference |
| Answer Facts | 30 | 确定性 Oracle 和边界文案 |
| Loop Safety | 10 | Tool 无错误、预算内、模型调用上限 |

## 7. 新增 Skill 的流程

1. 完成 `skill.json`、`SKILL.md`、References 和 MCP Tool 契约。
2. 为数据库或外部系统建立可重复 fixture 和 Oracle assertion。
3. 在 Skill 的 `evals/standard.json` 覆盖正向、缺参、越界、失败和边界场景。
4. 每题填写 `derived_from`，禁止只写“模型应该知道”。
5. 在 `skill.json.evaluation_suites` 注册题库。
6. 运行静态契约测试，再执行 `smoke` 和完整 `standard`。
7. 修改业务公式、seed、Tool schema 或 Skill 行为时，同时提升题库版本。

## 8. 执行命令

```bash
cd agent/python

# 只校验并列出题库，不调用模型
.venv/bin/python evals/live_benchmark.py --list

# 日常快速回归
.venv/bin/python evals/live_benchmark.py --tag smoke

# 发布前完整标准题库
.venv/bin/python evals/live_benchmark.py \
  --tag standard \
  --output ../../nino-agent-storage/live-benchmark.json
```

报告保留 suite ID、版本、来源、每题来源、Run ID、Tool、Reference、模型调用次数和各维度分数，
可以通过 Run Event API 复核。
