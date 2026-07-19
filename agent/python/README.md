# Python Agent Runtime API v0.16（Python 智能体运行时接口）

面向 App、Web 和 Desktop 的 API-first Python Agent Runtime，以 REST + SSE 作为当前产品入口。
CLI 不是产品入口，ACP 也不在当前实现范围内。

## 设计定位

Nino Agent 当前最准确的定位是：

> 一个以四个业务中立标准 Agent 为骨架，支持持久化任务图、确定性证据门禁、独立验证和故障恢复的
> 只读业务分析 Harness。

它不是简单的 Prompt + Model 包装。Planner 模型负责候选任务拆解，Worker 模型负责局部分析，
Orchestrator 模型只负责最终归并；Harness 负责能力边界、
执行预算、依赖调度、Tool 证据、完成条件、持久化状态和恢复决策。一次模型回答不等于任务完成，只有
TaskGraph 中必需节点及其 Gate 均满足后，Root Run 才能完成。

建议按以下顺序阅读：

1. 本 README：安装、运行、API、配置和能力边界。
2. [任务级 Harness 完整设计](../../doc/task-level-harness-design.md)：设计动机、领域模型、完整调用链、代码映射、恢复语义、Git 演进和取舍。
3. [gpt-5.4 Agent 启动手册](../../doc/gpt-5.4-agent-runbook.md)：真实模型启动、Tool Calling 联调与验收。
4. [React Web 前端 README](../../web/react/README.md)：浏览器客户端、单请求 SSE 和断线恢复。

## 架构

```text
FastAPI REST + SSE 接口宿主
    -> AgentRuntimeService
        -> AgentHarness 接口
            -> OrchestratorHarness（业务中立控制面）
                -> 确定性路由和候选过滤
                -> PlannerHarness（候选图或受控控制 Action）
                -> TaskGraphScheduler（DAG 校验和 Ready/Blocked 决策）
                -> ReActHarness 或 LangGraphReActHarness（Analyst/Verifier 工作节点）
                -> 无 Tool 的最终归并或仅历史归并
            -> AgentRegistry（四个业务中立标准 Agent）
            -> SkillRegistry（动态业务能力目录）
            -> ReferenceProvider（批准的按需上下文）
            -> ChatModel 接口（原生 OpenAI-compatible 或 LangChain）
            -> ToolProvider 接口
                -> McpServerRegistry
                    -> 每个配置 Server 对应一个 McpHttpToolClient
        -> AgentRepository 接口
            -> SqliteAgentRepository（默认持久化实现）
            -> InMemoryAgentRepository（测试或显式注入）
        -> ConversationContextManager
            -> 完整历史，或持久化摘要加最近轮次
```

Runtime 管理 Conversation、Run、上下文、取消和事件；Harness 管理 Prompt、Skill/Agent 策略和
ReAct 循环。Harness 不选择 MCP transport，而是调用 Framework `ToolProvider` Port。live 模式下
`McpServerRegistry` 聚合并路由多个 MCP Streamable HTTP Server。

Orchestrator 先执行正向关键词召回和候选 Agent/Skill 匹配，再调用 Planner。Planner 每个
revision 只做一次模型决策，可以通过结构化 Action 提交一个或多个候选 Specialist 节点、请求澄清、
拒绝请求，或选择受限的历史回答路径；它不能执行 MCP、持久化 Graph、调度 Worker 或直接生成最终
答案。Orchestrator 校验 Agent/Skill pair、Node ID、DAG、binding 和 Acceptance Contract 后才发出
`graph_planned/graph_reconciled`。`depends_on` 表达控制依赖，`input_bindings` 表达结构化结果传递。

## 项目结构和分层职责

```text
agent/python/
├── pyproject.toml                  # Python 包元数据和可选依赖
├── Dockerfile                     # Python Agent 部署镜像
├── README.md
├── src/
│   ├── api/                       # REST/SSE 传输适配器
│   │   ├── app.py                 # FastAPI 组装入口和端点
│   │   └── schemas.py             # 外部请求/响应 DTO
│   ├── runtime/                   # 持久化执行生命周期
│   │   ├── service.py             # Conversation、Run、事件、取消和并发
│   │   ├── task_graph.py          # Graph 投影、Node claim 和 Gate 收口
│   │   └── context.py             # Token 预算和上下文压缩
│   ├── harness/                   # Agent 推理和策略
│   │   ├── orchestrator.py        # 唯一控制面：校验、调度和归并
│   │   ├── planning.py            # 建议型 Planner 边界和候选 Graph Action
│   │   ├── scheduler.py           # 确定性 DAG 校验和 Ready/Blocked 选择
│   │   ├── validation.py          # 持久化 TaskGraph 一致性 lint
│   │   ├── loop.py                # Loop 预算、进度和停止策略
│   │   ├── react.py               # 轻量受控 ReAct 引擎
│   │   ├── langgraph.py           # 同一 Harness Port 的 LangGraph 实现
│   │   ├── skills.py              # 共享 Skill 加载、校验和路由
│   │   ├── agents.py              # Agent 角色、权限和委派图
│   │   ├── references.py          # 批准的按需 Reference 加载
│   │   └── documents.py           # YAML frontmatter 指令解析器
│   ├── framework/                 # 稳定实体和无基础设施依赖的 Port
│   ├── infrastructure/            # 可替换的 Model、MCP 和 Repository Adapter
│   ├── bootstrap.py               # Composition Root：选择并连接适配器
│   ├── demo.py                    # 确定性离线模型和工具
│   └── version.py                 # 服务版本
└── tests/                         # 分层、API、持久化、MCP 和 ReAct 测试
```

| 分层 | 负责内容 | 允许依赖 | 禁止包含 |
|---|---|---|---|
| `api` | HTTP/SSE 映射、校验、OpenAPI | Runtime 和 Composition Root | ReAct 策略、SQL、MCP 协议逻辑 |
| `runtime` | Conversation/Run 生命周期、上下文、检查点协调、事件 | Framework Port | Prompt 构造、Tool 选择、传输 SDK |
| `harness` | Prompt、Skill/Agent 策略、ReAct 步骤、Reference 和委派 | Framework Port 和共享文件 | FastAPI、SQLite 或 MCP 传输细节 |
| `framework` | 稳定实体和 Port | Python 标准库 | FastAPI、LangChain、LangGraph、httpx、SQLite 实现 |
| `infrastructure` | Model、MCP 和 Repository Adapter | Framework Port 和外部 SDK | Agent 规划或业务回答策略 |

依赖方向向内：`api -> runtime -> framework`、`harness -> framework`、
`infrastructure -> framework`。`bootstrap.py` 是唯一可以选择具体 Engine 和 Adapter 的
Composition Root。共享 Skill、Agent 和 Reference 保留在 `agent/shared`；Python 项目只读加载，不
维护私有副本。

使用显式分层导入：

```python
from harness import ReActHarness
from runtime import AgentRuntimeService
from framework import ToolProvider
from infrastructure.mcp import McpServerRegistry
```

项目没有 `src/nino_agent_runtime/` 包装层；`api`、`runtime`、`harness`、`framework` 和
`infrastructure` 都是 `src` 下的直接顶层包。生成的 `nino_agent_runtime.egg-info/` 只包含打包元数据，
已被 Git 忽略，可随时删除；它不是源码，执行 `pip install -e .` 时可能重新生成。

## 安装

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

只有选择相应框架时才安装可选依赖：

```bash
.venv/bin/pip install -e '.[frameworks,test]'
```

## 启动 API

```bash
.venv/bin/python -m uvicorn api.app:app \
  --host 127.0.0.1 \
  --port 8090 \
  --reload
```

OpenAPI 入口：

- Swagger 交互文档：`http://127.0.0.1:8090/docs`
- OpenAPI JSON 描述：`http://127.0.0.1:8090/openapi.json`
- 健康检查 Health：`http://127.0.0.1:8090/health`

gpt-5.4 真实模式启动和 Tool Calling 验收见
[gpt-5.4 Agent 启动手册](../../doc/gpt-5.4-agent-runbook.md)。任务级 Harness 架构、执行语义、恢复边界和
Git 演进见[任务级 Harness 完整设计](../../doc/task-level-harness-design.md)。

## 当前能力边界

| 能力 | 状态 | 当前含义 |
|---|---|---|
| REST + SSE Runtime | 已实现 | 持久化 Conversation/Run、可重放事件、取消和查询 API |
| 四个标准 Agent | 已实现 | 业务中立的 Orchestrator、Planner、Analyst、Verifier，角色边界由代码约束 |
| 动态 Skill 路由 | 已实现 | 正向关键词召回；未命中时只允许显式 opt-in 的语义 fallback，并按能力结构化拒绝 |
| 建议型 Planner | 已实现 | 只提出候选节点或受控决定；没有 MCP、持久化、调度和直接回答权限 |
| 通用 Analyst/Verifier | 已实现 | 选定 Skill 的新上下文、角色策略 Tool 过滤，不复制业务专用 Agent |
| TaskGraph 和 DAG 调度 | 已实现 | 持久化 Node、依赖、Gate、Attempt、并行 Ready Node 和 blocked 传播 |
| Fingerprint 安全复用 | 已实现 | 可执行合同、Skill 版本、binding 和依赖 Fingerprint 全部一致才复用 |
| Reconcile lineage | 已实现 | 显式 repair 只 supersede 失败/blocked 工作；Completed 历史不可变 |
| Assurance 修复 | 已实现 | Completed Worker 验证失败时创建不 supersede、不依赖原节点的独立 repair Node |
| 仅历史追问 | 已实现 | 可解释、比较、改写或计算既有回答；新事实仍要求 Tool Evidence |
| Evidence Gate | 已实现 | 事实性完成要求成功的非 Reference Tool Observation 和合同检查 |
| 独立 Verifier | 已实现 | 独立上下文、重新查询证据并提交结构化 verdict |
| 恢复 | 部分实现 | Root replay 并复用稳定 Completed Node；不从模型/Tool 中间或任意 Ready Node 精确继续 |
| 上下文压缩 | 已实现 | 保留原始消息，同时持久化提取式摘要和最近轮次 |
| 多 MCP 隔离 | 已实现 | required/optional Server、Tool 名唯一，以及发现结果与 Skill/Agent 策略交集 |
| 人工审批 | 未实现 | `awaiting_approval` 仅为领域预留状态，没有审批 API 或推进流程 |
| 写操作和幂等 | 未实现 | 当前安全和恢复结论只适用于只读分析 |
| 身份、租户和合规审计 | 未实现 | 明确不在当前执行内核范围内 |
| 精确分布式恢复 | 未实现 | SQLite 和进程内调度不是跨主机持久化队列 |

“确定性 Gate”表示状态、证据、依赖和契约检查由代码执行，不表示系统已经形式化证明了业务结论。
因此当前可以称为“完整任务级 Harness”，但不能称为“功能完备的企业 Agent 平台”。

## Git 演进

| Commit | 演进阶段 | 架构含义 |
|---|---|---|
| `4fd8492` | 初始占位 | 仓库占位，不代表有效架构能力 |
| `ee55b76` | 项目初始化 | 建立 FastAPI/SSE、分层 Runtime、ReAct/LangGraph、初始 Agent、SQLite 和 MCP 基线 |
| `9e67f80` | Skill 编排和真实评测 | 强化路由、Tool 证据、结构化澄清，并加入 GPT-5.4 联调与评测套件 |
| `be4427b` | 任务级 Harness 内核 | 引入 TaskGraph、DAG 调度、独立验证、恢复复用、输入绑定和合同 |
| `f13ed93` | 真实数据分析和固定评测 | 完善 PostgreSQL 数据集、标准题库、真实分析链路和小规模 Eval |
| `facbc9e` / `0.14.0` | Planner 与通用 Agent 分离 | 拆出 `nino.planner`，将 Analyst/Verifier 去业务化，Orchestrator 保持唯一控制面 |
| `0.16.0` | 流式回答 | Native 模型流式聚合，通过 Run SSE 暴露最终 `answer_delta`，内部 Agent 输出隔离 |
| `175c506` | 追问和 Assurance 修复 | 增加受限历史追问；验证失败时保留 Completed Worker 并创建独立修复节点 |

这不是按提交消息推测的路线图；完整设计文档同时使用对应提交的代码 diff 与当前代码交叉验证。

## API 调用流程

创建会话：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"July data analysis"}'
```

交互式客户端可在同一个 HTTP 响应中提交消息并消费完整 Agent 事件流：

```bash
curl -N http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/messages/stream \
  -H 'Content-Type: application/json' \
  -d '{"content":"查询订单 DEMO-202607-001 的收入、成本和毛利"}'
```

事件流以 `run_accepted` 开始，随后返回已持久化的模型、Tool 和进度事件，最后以包含权威状态、回答和
错误字段的 `run_result` 结束。HTTP 响应断开不会取消持久化 Run。

异步兼容端点会立即返回 `202 Accepted` 和 `run_id`：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"查询订单 DEMO-202607-001 的收入、成本和毛利"}'
```

重连或重放已有 Run：

```bash
curl -N http://127.0.0.1:8090/api/v1/runs/{run_id}/events/stream
```

最终用户回答应按事件顺序拼接每个 `answer_delta.data.delta`。Planner、Analyst 和 Verifier 的内部模型
token 刻意不对外暴露。终态 Run 仍是权威完整回答，并支持通过 `Last-Event-ID` 重连或重放。

读取 Run 状态，或从指定事件序号续接：

```bash
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}
curl -s 'http://127.0.0.1:8090/api/v1/runs/{run_id}/events?after=6'
```

取消运行：

```bash
curl -s -X POST http://127.0.0.1:8090/api/v1/runs/{run_id}/cancel
```

## API 端点

| 方法 | 路径 | 用途 |
|---|---|---|
| `GET` | `/health` | 存活状态和 Runtime 模式 |
| `GET` | `/api/v1/skills` | 查询已加载 Skill |
| `GET` | `/api/v1/agents` | 查询四个 Agent 及其风险、能力和 Tool 策略 |
| `GET` | `/api/v1/mcp/servers` | 查询多 MCP 状态；`?discover=true` 触发发现 |
| `POST` | `/api/v1/conversations` | 创建会话 |
| `GET` | `/api/v1/conversations` | 列出会话 |
| `GET` | `/api/v1/conversations/{id}` | 读取会话 |
| `GET` | `/api/v1/conversations/{id}/messages` | 读取会话消息 |
| `GET` | `/api/v1/conversations/{id}/context` | 读取持久化上下文压缩状态 |
| `GET` | `/api/v1/conversations/{id}/runs` | 读取会话 Run |
| `POST` | `/api/v1/conversations/{id}/messages/stream` | 提交消息并在同一响应中流式返回完整 Run |
| `POST` | `/api/v1/conversations/{id}/messages` | 为异步兼容排队一个 ReAct Run |
| `GET` | `/api/v1/runs/{id}` | 读取 Run 状态和结果 |
| `POST` | `/api/v1/runs/{id}/cancel` | 取消 queued/running Run |
| `GET` | `/api/v1/runs/{id}/events` | 读取可重放事件历史 |
| `GET` | `/api/v1/runs/{id}/events/stream` | 重连或重放持久化 SSE 事件 |
| `GET` | `/api/v1/runs/{id}/loop-checkpoint` | 读取最新 Loop 状态，可选 `kind` 过滤 |
| `GET` | `/api/v1/runs/{id}/task-graph` | 读取完整持久化 Graph 快照 |
| `GET` | `/api/v1/runs/{id}/task-graph/lint` | 校验依赖、Gate 和 Attempt 不变量 |
| `GET` | `/api/v1/runs/{id}/task-graph/nodes` | 读取 Task Node |
| `GET` | `/api/v1/runs/{id}/task-graph/gates` | 读取验收/证据 Gate |
| `GET` | `/api/v1/runs/{id}/task-graph/attempts` | 读取执行 Attempt 和 lease 状态 |

## Runtime 选择

切换 Engine 或 Model Adapter 不会改变 API 契约。

| 变量 | 取值 | 用途 |
|---|---|---|
| `NINO_RUNTIME_MODE` | `demo`, `live` | `demo` 是确定性模式，不调用外部服务 |
| `NINO_AGENT_ENGINE` | `lightweight`, `langgraph` | 选择 ReAct Loop 实现 |
| `NINO_MODEL_ADAPTER` | `native`, `langchain` | 选择直接 OpenAI-compatible HTTP 或 LangChain |
| model | 固定 `gpt-5.4` | 写死在 `bootstrap.py`，没有模型名环境变量 |
| `OPENAI_API_KEY` | secret | `live` 必填，只从进程环境读取 |
| `INCERRY_OPENAI_BASE_URL` | OpenAI-compatible `/v1` URL | `live` 必填 |
| `NINO_MODEL_THINKING` | 空、`enabled`、`disabled` | 可选网关思考模式控制 |
| `NINO_MODEL_REASONING_EFFORT` | 空、`high`、`max` | 可选推理强度 |
| `NINO_MODEL_TIMEOUT_SECONDS` | 正秒数 | Model HTTP 超时，默认 150 秒 |
| `NINO_MCP_URL` | MCP endpoint | 默认 `http://127.0.0.1:8091/mcp` |
| `NINO_MCP_SERVERS` | JSON Server 数组 | 多 MCP 目录；为空时保留单 URL fallback |
| `NINO_STORAGE_PATH` | SQLite 文件 | 默认 `nino-agent-storage/nino-agent.db` |
| `NINO_MODEL_CONTEXT_TOKENS` | 模型上下文窗口 | 默认 128K，应设置为实际模型限制 |
| `NINO_CONTEXT_RESERVED_TOKENS` | 非历史预留 | 默认 32K，留给指令、Tool、Observation 和输出 |
| `NINO_CONTEXT_RECENT_TOKENS` | 最近历史预算 | 原样保留最新约 48K token |
| `NINO_CONTEXT_SUMMARY_TOKENS` | 摘要预算 | 最多持久化约 12K token 的旧历史摘要 |
| `NINO_LOOP_HARD_MAX_STEPS` | 正整数 | 模型决策步骤硬上限，默认 8 |
| `NINO_LOOP_HARD_MAX_ACTIONS` | 1-100 | 规划和 Tool Action 硬上限，默认 32 |
| `NINO_LOOP_HARD_TIMEOUT_SECONDS` | 1-3600 | Runtime Loop 安全兜底上限，默认 3600 秒；正常停止由步骤、失败和无进展预算控制 |
| `NINO_LOOP_HARD_MAX_CONSECUTIVE_FAILURES` | 1-20 | 连续失败硬上限，默认 3 |
| `NINO_LOOP_HARD_MAX_NO_PROGRESS_STEPS` | 1-20 | 无进展步骤硬上限，默认 3 |
| `NINO_GRAPH_MAX_PARALLEL_NODES` | 正整数 | 进程内 Task Node 全局并发上限 |

首次运行 live 模式建议使用依赖最少的配置：

```bash
export NINO_RUNTIME_MODE=live
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=native
export OPENAI_API_KEY='<your-key>'
export INCERRY_OPENAI_BASE_URL='http://core.dns-pro.net:13001/v1'
export NINO_MCP_URL=http://127.0.0.1:8091/mcp
```

Docker Compose 固定使用 `NINO_RUNTIME_MODE=live`、`NINO_AGENT_ENGINE=lightweight` 和
`NINO_MODEL_ADAPTER=native`。它从宿主 shell 注入 `OPENAI_API_KEY`，缺少 Key 时 Compose 校验失败。
Demo 模式只用于本地测试或显式构造的非 Docker Runtime。

Runtime 忽略 `NINO_MODEL_NAME`、`NINO_MODEL_API_KEY`、`NINO_MODEL_BASE_URL`、`OPENAI_MODEL` 和
`OPENAI_BASE_URL`。禁止把 API Key 写入项目文件。

多个 MCP Server：

```bash
export NINO_MCP_SERVERS='[{"id":"nino-data","url":"http://127.0.0.1:8091/mcp","required":true},{"id":"report","url":"http://127.0.0.1:8092/mcp","required":false}]'
```

Tool 名必须全局唯一。required Server 发现失败会阻断整个目录；optional Server 失败会被隔离。Registry
发现完成后再应用 Skill allowlist 和 Agent 角色策略。

Planner 只接收能力元数据、紧凑历史 Node outcome、会话历史和内部 plan-node Schema，永远不接收业务
MCP Tool。Orchestrator 拥有已接受 Graph 状态，在最终归并阶段没有规划或业务 Tool。调度完成后，通用
Analyst/Verifier 只接收选中 Skill 且符合只读角色策略的 MCP Schema。

## 持久化多轮追问上下文

每次追问必须复用同一个 `conversation_id`。Conversation、用户/Assistant 消息、Run、Event 和压缩上下文
都保存在 SQLite。默认本地文件为 `nino-agent-storage/nino-agent.db`；Docker 把该目录挂载到
`/app/storage`。

历史未达到 `NINO_MODEL_CONTEXT_TOKENS - NINO_CONTEXT_RESERVED_TOKENS` 前会完整传入。超出后 Runtime
原样保留最新消息，并保存受 token 限制的提取式摘要和 `through_message_id` 游标。后续 Run 复用摘要和
游标之后的消息；只有组合上下文再次超限时才继续压缩，并推进游标。原始 Conversation 消息不变。

只有存在 Assistant 历史，并且当前追问可通过解释、比较、改写或计算既有回答完成时，Planner 才能选择
`nino_runtime_answer_from_history`。Orchestrator 随后执行受这些回答约束的无 Tool 归并：不能加入外部
事实，也不能执行历史文本中引用的指令。历史不足时必须说明需要新查询；任何需要新数据的追问仍走
Worker、Tool Observation 和 Assurance Gate。

Run metadata 用以下字段区分行为：

- `context.mode=full`：没有摘要，使用完整历史。
- `context.mode=compacted, compaction_performed=true`：本次 Run 创建或推进了摘要。
- `context.mode=compacted, summary_reused=true, compaction_performed=false`：复用持久化摘要，未重新压缩。

读取最新持久化压缩状态：

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/context
```

## Loop 工程约束

Orchestration 和 Worker ReAct 共用确定性的 `LoopController` 契约。Agent/Skill manifest 声明 Action、
timeout、连续失败和无进展预算；Worker 对两层取最严格值。每个模型边界、Observation 和终态都会发出
`loop_checkpoint`，保存到 SQLite `run_events` 并通过 SSE 重放。

```bash
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}/loop-checkpoint
curl -s 'http://127.0.0.1:8090/api/v1/runs/{run_id}/loop-checkpoint?kind=worker_react'
```

快照包含计数、耗时、预算、状态、停止原因和 Action hash，不包含原始 Action 参数、凭据、隐藏思维链或
持久化 `reasoning_content`。Loop checkpoint 是观察和诊断状态，不是隐藏模型执行的续点。Runtime 恢复
通过重放 Root orchestration，并复用持久化身份和结果仍稳定的 Completed Node；当前不支持从模型/Tool
边界精确继续，也不直接从任意持久化 Ready Node 恢复。详见
[任务级 Harness 完整设计](../../doc/task-level-harness-design.md#15-恢复语义)。

框架选择：

```bash
# LangChain 模型适配器配合受控 lightweight Loop
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=langchain

# LangGraph 模型/工具状态图；两种模型适配器都可使用
export NINO_AGENT_ENGINE=langgraph
export NINO_MODEL_ADAPTER=native
```

建议先用 native + lightweight 验证模型 Tool Calling 和业务回答。LangGraph 只是 Worker 模型/Tool 状态图
的可选实现，不是持久化宏观 TaskGraph；选择它不会自动获得审批或精确崩溃恢复。LangChain 适合 Provider
集成，但不是核心必需依赖。

## Skill、Reference 和 Agent

每个 `SKILL.md` 和 `AGENT.md` 都以 YAML frontmatter 开头。Markdown 负责角色的 `name` 和
`description`；JSON manifest 保存由机器强制执行的权限和预算。

```markdown
---
name: nino-data-analysis
description: |
  只读 Nino Data 分析工作流，用于订单查询和统计。
---
```

Skill Reference 在 `skill.json` 中声明，只能通过 `nino_runtime_load_reference` 加载。模型提供 Reference
ID，不能提供文件路径。Runtime 检查目录边界、文件存在性和字符预算，并通过 `reference_loaded` 事件
记录文档 SHA256。

四个标准 Agent 是 `nino.orchestrator`、`nino.planner`、`nino.analyst` 和 `nino.verifier`，均保持业务
中立。代码职责如下：

| Agent | 负责 | 禁止事项 |
|---|---|---|
| `nino.orchestrator` | 路由、校验 proposal、接受 revision、调度、Gate 和最终归并 | 执行业务 MCP 或把 Graph Truth 交给模型 |
| `nino.planner` | 提出有边界的候选节点、依赖、binding、合同或受控决定 | 持久化、调度、执行 MCP 或直接回答用户 |
| `nino.analyst` | 执行一个选定只读 Skill，返回有证据的 Node Result | 规划 Root Run 或声称完成独立验证 |
| `nino.verifier` | 独立重查最小证据并提交结构化 verdict | 把 Analyst 文本当证据或修补其结论 |

路由先执行正向关键词召回。关键词命中会直接限制候选目录；未命中时只有
`semantic_fallback=true` 的 Skill 可进入模型辅助路由。Skill 只声明自身支持的意图和能力，不维护
排除意图列表。Planner 可提出有边界的 TaskGraph Node、澄清、
拒绝或仅历史回答决定。Orchestrator 校验 proposal、发出已接受 Graph revision、执行调度，并保持唯一
Graph 控制面。规划使用
`nino_runtime_submit_task_graph_node(agent_id, skill_id, task, context, depends_on, input_bindings, acceptance_contract)`;
`nino_runtime_answer_from_history` 只有存在 Assistant 历史时才暴露。选中的通用 Analyst/Verifier 获得新
上下文，加载选定 Skill 和 Reference，并独占批准的 MCP Tool。有效 Tool 是 MCP 发现结果、
`Skill.allowed_tools` 和 Agent 角色策略的交集。因此兼容的新只读业务通常增加 Skill、Reference、MCP
集成、固定题库和测试，不修改或复制 Agent manifest。

子模型、Reference 和 Tool 事件通过 `parent_run_id`、`child_run_id`、`agent_id` 和 `skill_id` 合并进父
Run。Worker 只有在成功 Tool Observation 后才能输出事实性答案；缺参必须使用经过校验的
`nino_runtime_request_clarification`，不能返回普通 Assistant 文本。一般问题可进入显式 opt-in 的语义
fallback，但仍必须通过结构化拒绝完成；不能声称所有未命中请求都在模型调用前拒绝。

Specialist 完成但独立 Assurance Gate 失败时，其 completed status、result 和 evidence 保持冻结。紧凑
Planner 状态标记为 `work_status=completed`、`assurance_status=failed` 和 `supersedable=false`。下一版
revision 必须提交新 ID 的独立只读 repair Node，不依赖原 Node，也不设置 `supersedes_node_id`。
Orchestrator 会在确定性 Graph 校验前移除无效 supersedes。显式 supersedes 只适用于失败或 blocked
工作，并可能使尚未完成的受影响下游失效。

## 测试

当前 `0.16.0` 代码的完整 Python 单元测试共 73 项。实现变更后执行：

```bash
.venv/bin/python -m unittest discover -s tests -v
```
