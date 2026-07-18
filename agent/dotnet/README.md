# .NET 实现（预留）

该目录为未来的 .NET Agent Runtime 预留，并将直接加载 `agent/shared` 下的共享契约。
当前独立的 .NET Nino Data MCP Server 位于 `mcp/dotnet`，它不是 .NET Agent Runtime。

目标分层为 `Api`、`Runtime`、`Harness`、`Framework` 和 `Infrastructure`。Skill、Agent、Reference 和
JSON Schema 继续保留在 `agent/shared`，由实现以只读方式加载。
