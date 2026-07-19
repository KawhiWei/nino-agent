# 共享 Agent 契约

`agent/shared` 是所有语言 Agent 实现共同使用的语言无关事实源。

```text
shared/
├── contracts/     # 用于机器校验的 JSON Schema
├── skills/        # 能力、Tool 白名单、Reference 和标准评测套件
└── agents/        # 业务中立的 Orchestrator、Planner、Analyst、Verifier 及角色策略
```

规则：

1. 共享文件不能导入或引用 Python、Node.js 或 .NET 实现代码。
2. `skill.json.id` 和 `agent.json.id` 是稳定的跨语言身份。
3. `SKILL.md` 和 `AGENT.md` 的 frontmatter（头部元数据）负责展示名称和描述。
4. Reference 路径相对于所属 Skill 目录，Runtime 必须检查目录边界。
5. 每种语言的 Runtime 在暴露 Skill 或 Agent 前都必须校验 JSON 契约。
6. Tool 名称指向 MCP 能力；共享 Skill 不得包含 SQL、凭据或传输地址。
7. 各语言实现可以优化 Harness/Runtime 内部结构，但必须保持共享 ID、白名单语义、委派深度和对外事件。
8. Orchestrator 从 Registry 构造候选能力后交给 Planner。Planner 只提出候选节点或受控决定；
   Orchestrator 独占校验、持久化、调度、归并和完成权限。
9. 通用 Analyst 和 Verifier 在新上下文中加载选定 Skill。有效 Tool 是 MCP 发现结果、Skill 白名单和
   Agent 角色策略的交集。
10. Agent 和 Skill 的 `loop` 值是策略上限。Runtime 对每个字段取最严格值；业务定义只能收紧预算。
11. Skill 只声明自身支持的 `intent_keywords` 和 `capabilities`；未匹配任何已注册能力的请求必须拒绝。
12. 生产 Skill 应声明位于 `question-banks/<capability>/` 的版本化 `evaluation_suites`。每个案例必须记录
    `derived_from` 来源，只能引用该 Skill 拥有的 Tool 和 Reference。
13. 标准业务中立 Agent 是 `nino.orchestrator`、`nino.planner`、`nino.analyst` 和
    `nino.verifier`。兼容的新只读业务通常增加 Skill 和 MCP Server，而不是增加 Agent manifest。
14. `nino_runtime_answer_from_history` 仅在存在 Assistant 历史时提供，只能解释、比较、改写或计算先前
    已接受回答；需要新事实时仍必须调用 Worker 和 Tool。

Python 通过 `NINO_SKILLS_PATH` 和 `NINO_AGENTS_PATH` 加载此目录。未来 Node.js 和 .NET 实现必须加载
同一目录，不能把内容复制到各自项目中形成分叉。

## 标准 Agent 契约

| ID | 角色 | 共享策略 |
|---|---|---|
| `nino.orchestrator` | 唯一控制面 | 不绑定业务 Skill/Tool；负责已接受 Graph 的执行和最终归并 |
| `nino.planner` | 建议型规划器 | 不绑定业务 Skill/Tool；只提出候选节点或受控控制决定 |
| `nino.analyst` | 通用工作节点 | 接受兼容的 `read-only` Skill；Tool 来自选定 Skill 策略 |
| `nino.verifier` | 通用独立验证节点 | 使用兼容的只读 Skill 独立重跑最小证据 |

通用 Analyst/Verifier 的 `allowed_skills` 为空不代表无限权限，也不代表没有能力。在
`tool_policy=selected-skill-only` 下，Runtime 先校验 `accepted_risk_levels/accepted_capabilities`，再只
暴露同时存在于 MCP 发现结果和 `Skill.allowed_tools` 中的 Tool。显式白名单仍可用于未来的窄角色。

已接受的 TaskGraph Node 携带规范化 `node_fingerprint`。只有 logical ID 和 Fingerprint 都一致时，
Runtime 才能复用 Completed Node。同一 logical ID 的 Fingerprint 变化会创建新物理版本，同时冻结旧
Completed 的 status/result/gate。显式 repair 的 `supersedes_node_id` 只能替换早期失败或 blocked
工作；受影响且未完成的下游进入 `superseded`，Completed 历史保持不可变。

如果 Specialist 已 Completed 但 Assurance Gate 失败，Planner 必须创建独立只读 repair Node，不设置
`supersedes_node_id`，也不依赖原 Completed Node。该 lineage（演进关系）属于跨语言 Harness 行为，
但当前仍不承诺从任意 Ready Node 精确恢复。
