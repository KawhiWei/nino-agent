# Nino Agent 多语言实现

服务端实现按语言隔离。跨语言兼容依赖协议和 JSON 契约，而不是共享 Runtime 源码。

```text
agent/
├── shared/
│   ├── contracts/       # 语言无关的 JSON Schema
│   ├── question-banks/  # 跨语言共享的版本化固定评测题库
│   ├── skills/          # 工作流指令和按需参考资料
│   └── agents/          # 业务中立的 Orchestrator、Planner、Analyst 和 Verifier
├── python/
│   ├── src/             # API、Runtime、Harness、Framework 和 Infrastructure
│   ├── tests/           # Python 单元测试和契约测试
│   ├── pyproject.toml
│   └── Dockerfile
├── nodejs/              # 未来的 Node.js 实现
└── dotnet/              # 未来的 .NET Agent Runtime 实现
```

当前实现：

- [Python Agent Runtime（Python 智能体运行时）](./python/README.md)

所有语言实现都必须保持相同的角色边界：Planner 只提出候选图节点；Orchestrator 独占校验权和持久化
Graph 执行控制权；通用 Analyst/Verifier 加载选中的 Skill，并且只能使用策略兼容的只读 Tool。兼容的
新业务能力应增加 Skill、MCP 集成、Reference 和固定题库，而不是复制业务专用 Agent。

MCP Server 是独立部署，位于顶层 [`mcp`](../mcp) 目录。
