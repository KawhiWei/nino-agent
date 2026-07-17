# Nino Agent 多语言分层规范

## 1. 目标

Python、Node.js 和 .NET Agent 可以使用不同框架和实现方式，但必须遵守同一调用链、共享同一套 Skill/Agent 定义，并通过相同外部协议工作。

```text
API -> Runtime -> Harness -> Framework Ports -> Infrastructure
                    |
                    -> shared Skills / Agents / References
```

## 2. 仓库级目录

```text
agent/
├── shared/
│   ├── contracts/
│   ├── skills/
│   └── agents/
├── python/
│   ├── src/
│   ├── tests/
│   ├── pyproject.toml
│   └── Dockerfile
├── nodejs/
│   ├── src/                    # 后续实现
│   └── tests/
└── dotnet/
    ├── src/                    # 后续实现
    └── tests/
```

`agent/shared` 是唯一跨语言内容源。每种语言的 Docker 镜像或本地进程通过路径配置只读加载它，不能把 Skill 复制成语言私有版本。

## 3. 每种语言必须具有的五层

```text
<language>/
├── api/              # HTTP/SSE/ACP adapter，参数和事件映射
├── runtime/          # Session、Run、checkpoint、context、持久化协调、SSE
├── harness/          # 通用编排、能力发现、权限、Worker ReAct step、派发
├── framework/        # 无基础设施依赖的类型和 Ports
└── infrastructure/   # Model、MCP、SQLite/PostgreSQL、缓存等 Adapter
```

| 层 | 必须负责 | 禁止负责 |
|---|---|---|
| API | 接收请求、返回 DTO、SSE/ACP 映射 | ReAct、SQL、模型选择 |
| Runtime | 创建 Run、恢复历史、checkpoint、取消、事件、持久化协调 | Prompt 和 Tool 决策 |
| Harness | Orchestration Loop、Capability Catalog、Worker ReAct、Skill/Agent policy、Tool 过滤 | HTTP API、SQLite SQL |
| Framework | Message、Run、Tool、Context 类型和 Ports | SDK、网络、数据库实现 |
| Infrastructure | 模型 SDK、MCP Client/Registry、Repository 实现 | 业务规划和回答 |

这里的 `framework/` 是项目自己的稳定内核与 Ports，不是特指 LangChain。第三方框架按作用接入：

- LangChain 当前作为 `ChatModel` Adapter，位于 Infrastructure。
- LangGraph 当前作为 `AgentHarness` 的图执行实现，位于 Harness。
- LlamaIndex 等后续框架可以实现相同 Port，但不能让 API、Runtime 或共享 Skill 依赖其私有类型。

因此更换语言或第三方框架时，Conversation/Run/Event、Skill、Tool 和 MCP 的契约保持不变。

## 4. 当前 Python 映射

```text
src/
├── api/
├── runtime/
│   ├── service.py
│   └── context.py
├── harness/
│   ├── orchestrator.py
│   ├── loop.py
│   ├── react.py
│   ├── langgraph.py
│   ├── skills.py
│   ├── agents.py
│   ├── references.py
│   └── documents.py
├── framework/
│   ├── models.py
│   ├── conversation.py
│   ├── ports.py
│   └── repositories.py
├── infrastructure/
│   ├── mcp/
│   ├── sqlite.py
│   ├── openai_compatible.py
│   └── langchain_model.py
└── bootstrap.py
```

Python 实现不使用 `src/nino_agent_runtime/` 包装层，也不提供旧目录或旧类型兼容层。`api`、`runtime`、`harness`、`framework` 和 `infrastructure` 直接位于 `src/`。所有代码必须从对应分层 package 导入。

## 5. 共享 Skill 契约

每种语言必须实现相同的加载过程：

1. 扫描 `shared/skills/*/skill.json`。
2. 使用 `contracts/skill.schema.json` 校验机器配置。
3. 加载 `SKILL.md` 并校验 YAML frontmatter。
4. 只允许访问 `skill.json.references` 声明的相对路径。
5. Orchestrator 只读取 Agent/Skill 描述、capabilities 和 risk metadata，并选择兼容组合。
6. Specialist 加载完整 Skill 后，将 `allowed_tools` 与 Agent allowlist 和 MCP Registry 目录取交集。
7. 使用 JSON `id` 作为跨语言唯一身份，不能使用目录名代替。

Agent 定义同理加载 `shared/agents/*/agent.json` 和 `AGENT.md`。

## 6. 跨语言稳定契约

以下语义不能由某种语言自行改变：

- Conversation、Run、Message 和 Event 的 ID/状态语义。
- Run 状态：`queued/running/completed/failed/cancelled`。
- Event sequence 在单个 Run 中严格递增。
- Skill、Agent、Reference 和 Tool ID。
- Tool allowlist 取交集的规则。
- 动态候选发现、Specialist Skill 白名单和最大派发深度。
- `nino_runtime_dispatch_agent` 参数与结构化 dispatch result 语义。
- Loop kind/status/stop reason、预算合并和 `loop_checkpoint` 字段语义。
- MCP 使用标准协议，产品客户端协议使用 REST/SSE。

语言内部的类名、异步库、图框架和 Repository SDK 可以不同。

## 7. 后续语言落地顺序

1. 先实现 Framework 类型和 Port，并通过共享 JSON fixture contract tests。
2. 实现 Infrastructure MCP Registry，验证多 Server 发现和路由。
3. 实现 Harness `run(request)` 与 `step(state)`；前者组织有界 ReAct，后者只做一次模型决策。
4. 实现 Runtime Session/Run/Event 和持久化。
5. 最后接 REST/SSE API Adapter。

不要从复制 Python 源码开始；应从共享契约和行为测试开始。
