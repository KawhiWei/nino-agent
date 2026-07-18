# Nino Agent Clients

`web` 目录按客户端技术栈拆分实现，便于不同端共享 Nino Agent 的 REST + SSE 协议，同时保持各自独立构建。

| Directory | Stack | Status |
|---|---|---|
| [`react`](./react) | React + TypeScript + Vite + Semi Design | 可运行 |

React 版本提供自然语言会话、单请求 SSE 流式回答、active Run 断线恢复、持久化多轮上下文和任务取消。
其他客户端可在此目录下新增独立子目录，例如 `react-native` 或 `flutter`。
