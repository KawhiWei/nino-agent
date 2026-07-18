# Nino Agent Implementations

Server implementations are separated by language. Cross-language compatibility is based on protocols and JSON contracts, not shared runtime code.

```text
agent/
├── shared/
│   ├── contracts/       # Language-neutral JSON schemas
│   ├── question-banks/  # Versioned cross-language fixed evaluation suites
│   ├── skills/          # Workflow instructions and on-demand references
│   └── agents/          # Business-neutral Orchestrator, Planner, Analyst, and Verifier
├── python/
│   ├── src/             # api/runtime/harness/framework/infrastructure
│   ├── tests/           # Python unit and contract tests
│   ├── pyproject.toml
│   └── Dockerfile
├── nodejs/              # Future Node.js implementation
└── dotnet/              # Future .NET Agent Runtime implementation
```

Current implementations:

- [Python Agent Runtime](./python/README.md)

All language implementations must preserve the same role boundary: Planner proposes candidate graph
nodes; Orchestrator alone validates and owns durable graph execution; generic Analyst and Verifier
load the selected Skill and only its policy-compatible read-only tools. A compatible new business
capability should add a Skill, MCP integration, References, and a fixed question bank rather than a
business-specific Agent.

MCP servers are independent deployments under the top-level [`mcp`](../mcp) directory.
