# Nino Agent Implementations

Server implementations are separated by language. Cross-language compatibility is based on protocols and JSON contracts, not shared runtime code.

```text
agent/
├── shared/
│   ├── contracts/       # Language-neutral JSON schemas
│   ├── skills/          # Workflow instructions and on-demand references
│   └── agents/          # Primary and specialist role definitions
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

MCP servers are independent deployments under the top-level [`mcp`](../mcp) directory.
