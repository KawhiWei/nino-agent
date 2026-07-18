# Python Agent Runtime API v0.15

面向 App、Web 和 Desktop 的 API-first Python Agent Runtime，以 REST + SSE 作为当前产品入口。
CLI 不是产品入口，ACP 也不在当前实现范围内。

## Design Positioning

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
3. [gpt-5.4 Agent Runbook](../../doc/gpt-5.4-agent-runbook.md)：真实模型启动、Tool Calling 联调与验收。

## Architecture

```text
FastAPI REST + SSE Host
    -> AgentRuntimeService
        -> AgentHarness port
            -> OrchestratorHarness (business-neutral control plane)
                -> deterministic route and candidate filtering
                -> PlannerHarness (candidate TaskGraph proposal only)
                -> TaskGraphScheduler (DAG validation and Ready/Blocked decisions)
                -> ReActHarness or LangGraphReActHarness (Analyst/Verifier workers)
                -> final reconciliation with no tools
            -> AgentRegistry (four business-neutral standard Agents)
            -> SkillRegistry (dynamic business capability catalog)
            -> ReferenceProvider (approved on-demand context)
            -> ChatModel port (native OpenAI-compatible or LangChain)
            -> ToolProvider port
                -> McpServerRegistry
                    -> one McpHttpToolClient per configured server
        -> AgentRepository port
            -> SqliteAgentRepository (default, persistent)
            -> InMemoryAgentRepository (tests/explicit injection)
        -> ConversationContextManager
            -> full history or persisted compacted summary + recent turns
```

Runtime 管理 Conversation、Run、上下文、取消和事件；Harness 管理 Prompt、Skill/Agent 策略和
ReAct 循环。Harness 不选择 MCP transport，而是调用 Framework `ToolProvider` Port。live 模式下
`McpServerRegistry` 聚合并路由多个 MCP Streamable HTTP Server。

Orchestrator 先执行确定性排除、关键词召回和候选 Agent/Skill 匹配，再调用 Planner。Planner 每个
revision 只做一次模型决策，通过结构化 Action 提交一个或多个候选 Specialist 节点，不能执行 MCP、
持久化 Graph、调度 Worker 或生成最终答案。Orchestrator 校验 Agent/Skill pair、Node ID、DAG、
binding 和 Acceptance Contract 后才发出 `graph_planned/graph_reconciled`。`depends_on` 表达控制依赖，
`input_bindings` 表达结构化结果传递。

## Project Structure and Layer Responsibilities

```text
agent/python/
├── pyproject.toml                  # Python package metadata and optional dependencies
├── Dockerfile                     # Python Agent deployment image
├── README.md
├── src/
│   ├── api/                       # REST/SSE transport adapter
│   │   ├── app.py                 # FastAPI composition entry and endpoints
│   │   └── schemas.py             # External request/response DTOs
│   ├── runtime/                   # Durable execution lifecycle
│   │   ├── service.py             # Conversation, Run, events, cancellation and concurrency
│   │   ├── task_graph.py          # Durable Graph projection, Node claim and gate completion
│   │   └── context.py             # Token budgets and context compaction
│   ├── harness/                   # Agent reasoning and policy
│   │   ├── orchestrator.py        # Sole control plane: validate, persist, schedule, reconcile
│   │   ├── planning.py            # Advisory Planner boundary and candidate Graph Action
│   │   ├── scheduler.py           # Deterministic DAG validation and ready/blocked selection
│   │   ├── validation.py          # Persisted TaskGraph consistency lint
│   │   ├── loop.py                # Loop budgets, progress accounting and stop policy
│   │   ├── react.py               # Lightweight controlled ReAct engine
│   │   ├── langgraph.py           # LangGraph implementation of the same Harness Port
│   │   ├── skills.py              # Shared Skill loading, validation and routing
│   │   ├── agents.py              # Agent roles, permissions and delegation graph
│   │   ├── references.py          # Approved on-demand Reference loading
│   │   └── documents.py           # YAML frontmatter instruction parser
│   ├── framework/                 # Stable entities and infrastructure-free Ports
│   │   ├── loop.py                # Cross-language Loop state and stop-reason contracts
│   │   ├── task_graph.py          # Graph, Node, Gate, Attempt and acceptance entities
│   │   ├── models.py              # ReAct Message, Tool, Event and Result types
│   │   ├── conversation.py        # Conversation, Run and persisted context entities
│   │   ├── ports.py               # AgentHarness, ChatModel and ToolProvider Ports
│   │   └── repositories.py        # Persistence Ports
│   ├── infrastructure/            # Replaceable external adapters
│   │   ├── mcp/                   # Multi-server MCP config, client and registry
│   │   ├── sqlite.py              # Local persistent Repository adapter
│   │   ├── memory.py              # In-memory Repository for tests
│   │   ├── openai_compatible.py   # Native model adapter
│   │   └── langchain_model.py     # LangChain model adapter
│   ├── bootstrap.py               # Composition root: select and wire adapters
│   ├── demo.py                    # Deterministic offline model and tools
│   └── version.py                 # Service version
└── tests/                         # Layer, API, persistence, MCP and ReAct tests
```

| Layer | Owns | May depend on | Must not contain |
|---|---|---|---|
| `api` | HTTP/SSE mapping, validation, OpenAPI | Runtime and composition root | ReAct policy, SQL, MCP protocol logic |
| `runtime` | Conversation/Run lifecycle, context, checkpoint coordination, events | Framework Ports | Prompt construction, tool selection, transport SDKs |
| `harness` | Prompt, Skill/Agent policy, ReAct steps, References and delegation | Framework Ports and shared files | FastAPI, SQLite or MCP transport details |
| `framework` | Stable entities and Ports | Python standard library | FastAPI, LangChain, LangGraph, httpx, SQLite implementations |
| `infrastructure` | Model, MCP and Repository adapters | Framework Ports and external SDKs | Agent planning or business answer policy |

Dependency direction is inward: `api -> runtime -> framework`, `harness -> framework`, and
`infrastructure -> framework`. `bootstrap.py` is the only composition root allowed to select
concrete engines and adapters. Shared Skills, Agents and References remain in `agent/shared`; the
Python project loads them read-only and never keeps a private copy.

Use explicit layer imports:

```python
from harness import ReActHarness
from runtime import AgentRuntimeService
from framework import ToolProvider
from infrastructure.mcp import McpServerRegistry
```

There is no `src/nino_agent_runtime/` wrapper package. `api`, `runtime`, `harness`, `framework`,
and `infrastructure` are direct top-level packages under `src`. A generated
`nino_agent_runtime.egg-info/` directory contains packaging metadata only; it is Git-ignored and
can be deleted at any time. It is not source code and may be recreated by `pip install -e .`.

## Setup

```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[test]'
```

Install optional frameworks only when they are selected:

```bash
.venv/bin/pip install -e '.[frameworks,test]'
```

## Start API

```bash
.venv/bin/python -m uvicorn api.app:app \
  --host 127.0.0.1 \
  --port 8090 \
  --reload
```

OpenAPI:

- Swagger UI: `http://127.0.0.1:8090/docs`
- OpenAPI JSON: `http://127.0.0.1:8090/openapi.json`
- Health: `http://127.0.0.1:8090/health`

gpt-5.4 live setup and Tool Calling acceptance: [gpt-5.4 Agent Runbook](../../doc/gpt-5.4-agent-runbook.md).
Task-level Harness architecture, execution semantics, recovery boundaries, and Git evolution:
[Task-level Harness Complete Design](../../doc/task-level-harness-design.md).

## Current Capability Boundary

| Capability | Status | Current meaning |
|---|---|---|
| REST + SSE Runtime | Implemented | Durable Conversation/Run, replayable events, cancellation, and query APIs |
| Four standard Agents | Implemented | Business-neutral Orchestrator, Planner, Analyst, and Verifier with enforced role boundaries |
| Dynamic Skill routing | Implemented | Deterministic exclusion and keyword recall; opt-in semantic fallback when keywords do not match |
| Advisory Planner | Implemented | Proposes candidate nodes only; no MCP, persistence, scheduling, or final-answer authority |
| Generic Analyst/Verifier | Implemented | Fresh selected-Skill context, role-policy Tool filtering, and no business-specific Agent cloning |
| TaskGraph and DAG scheduling | Implemented | Durable Node, dependency, Gate, Attempt, parallel ready-node scheduling, and blocked propagation |
| Fingerprint-safe reuse | Implemented | Reuse requires identical executable contract, Skill version, bindings, and dependency fingerprints |
| Reconcile lineage | Implemented | Repair nodes explicitly supersede failed/blocked history and invalidate unfinished affected descendants |
| Evidence Gate | Implemented | Factual completion requires successful non-reference Tool Observation and contract checks |
| Independent Verifier | Implemented | Verifier uses a separate execution context, re-queries evidence, and submits a structured verdict |
| Recovery | Partial | Replays the Root plan and reuses stable completed nodes; does not resume inside a model/tool call or directly from every persisted Ready Node |
| Context compaction | Implemented | Persists an extractive summary plus recent turns while retaining raw conversation messages |
| Multi-MCP isolation | Implemented | Required/optional servers, unique Tool names, and MCP discovery ∩ Skill allowlist ∩ Agent role policy |
| Human approval | Not implemented | `awaiting_approval` is a reserved domain state only; there is no approval API or transition workflow |
| Write operations and idempotency | Not implemented | Current safety and recovery claims apply to read-only analysis |
| Identity, tenant isolation, and compliance audit | Not implemented | These are intentionally outside the current execution-kernel scope |
| Exact distributed resume | Not implemented | SQLite and in-process scheduling are not a cross-host durable queue |

“确定性 Gate”表示状态、证据、依赖和契约检查由代码执行，不表示系统已经形式化证明了业务结论。
因此当前可以称为“完整任务级 Harness”，但不能称为“功能完备的企业 Agent 平台”。

## Git Evolution

| Commit | Evolution | Architectural meaning |
|---|---|---|
| `4fd8492` | Initial placeholder | 仓库占位，不代表有效架构能力 |
| `ee55b76` | Project initialization | 建立 FastAPI/SSE、分层 Runtime、ReAct/LangGraph、初始 Orchestrator/Analyst/Verifier、SQLite 和 MCP 基线 |
| `9e67f80` | Skill orchestration and live evaluation | 强化严格路由、Tool 证据、结构化澄清，并加入 GPT-5.4 联调与评测套件 |
| `be4427b` | Task-level Harness kernel | 引入 TaskGraph/Node/Gate/Attempt、DAG 调度、独立验证、恢复复用、输入绑定和 Acceptance Contract |
| `f13ed93` | Real data analysis and fixed evaluation | 完善 PostgreSQL 12.18 数据集、标准题库、真实分析链路和小规模 Eval |
| `facbc9e` / `0.14.0` | Planner and generic Agent separation | 拆出 `nino.planner`，将 Analyst/Verifier 去业务化，并保持 Orchestrator 为唯一控制面 |
| `0.15.0 current design` | Fingerprint-safe Graph reconciliation | 严格 Completed 复用、revision lineage、显式 supersedes 和 Pending 后缀失效 |

这不是按提交消息推测的路线图；完整设计文档同时使用对应提交的代码 diff 与当前代码交叉验证。

## API Flow

Create a conversation:

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations \
  -H 'Content-Type: application/json' \
  -d '{"title":"July data analysis"}'
```

Submit a message. The API returns `202 Accepted` and a `run_id` immediately:

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/messages \
  -H 'Content-Type: application/json' \
  -d '{"content":"查询订单 DEMO-202607-001 的收入、成本和毛利"}'
```

Stream events:

```bash
curl -N http://127.0.0.1:8090/api/v1/runs/{run_id}/events/stream
```

Read run state or reconnect from an event sequence:

```bash
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}
curl -s 'http://127.0.0.1:8090/api/v1/runs/{run_id}/events?after=6'
```

Cancel:

```bash
curl -s -X POST http://127.0.0.1:8090/api/v1/runs/{run_id}/cancel
```

## Endpoints

| Method | Path | Purpose |
|---|---|---|
| `GET` | `/health` | Liveness and runtime mode |
| `GET` | `/api/v1/skills` | Discover loaded Skills |
| `GET` | `/api/v1/agents` | Discover all four Agents and their risk/capability/tool policies |
| `GET` | `/api/v1/mcp/servers` | Read multi-MCP discovery status; `?discover=true` triggers discovery |
| `POST` | `/api/v1/conversations` | Create a conversation |
| `GET` | `/api/v1/conversations` | List conversations |
| `GET` | `/api/v1/conversations/{id}` | Read a conversation |
| `GET` | `/api/v1/conversations/{id}/messages` | Read conversation messages |
| `GET` | `/api/v1/conversations/{id}/context` | Read persisted context-compaction state |
| `GET` | `/api/v1/conversations/{id}/runs` | Read conversation runs |
| `POST` | `/api/v1/conversations/{id}/messages` | Queue one ReAct run |
| `GET` | `/api/v1/runs/{id}` | Read run state/result |
| `POST` | `/api/v1/runs/{id}/cancel` | Cancel queued/running run |
| `GET` | `/api/v1/runs/{id}/events` | Read replayable event history |
| `GET` | `/api/v1/runs/{id}/events/stream` | Stream SSE events |
| `GET` | `/api/v1/runs/{id}/loop-checkpoint` | Read latest persisted Loop state; optional `kind` filter |
| `GET` | `/api/v1/runs/{id}/task-graph` | Read complete durable Graph snapshot |
| `GET` | `/api/v1/runs/{id}/task-graph/lint` | Validate Graph dependency, Gate and Attempt invariants |
| `GET` | `/api/v1/runs/{id}/task-graph/nodes` | Read Task Nodes |
| `GET` | `/api/v1/runs/{id}/task-graph/gates` | Read acceptance/evidence Gates |
| `GET` | `/api/v1/runs/{id}/task-graph/attempts` | Read execution attempts and lease state |

## Runtime Selection

The API contract does not change when the engine or model adapter changes.

| Variable | Values | Purpose |
|---|---|---|
| `NINO_RUNTIME_MODE` | `demo`, `live` | `demo` is deterministic and does not call external services. |
| `NINO_AGENT_ENGINE` | `lightweight`, `langgraph` | Select the ReAct loop implementation. |
| `NINO_MODEL_ADAPTER` | `native`, `langchain` | Select direct OpenAI-compatible HTTP or LangChain. |
| model | fixed `gpt-5.4` | Hardcoded in `bootstrap.py`; there is no model-name environment variable. |
| `OPENAI_API_KEY` | secret | Required in `live` mode; read from the process environment only. |
| `INCERRY_OPENAI_BASE_URL` | OpenAI-compatible `/v1` URL | Required in `live` mode. |
| `NINO_MODEL_THINKING` | empty, `enabled`, `disabled` | Optional compatible-gateway thinking control. |
| `NINO_MODEL_REASONING_EFFORT` | empty, `high`, `max` | Optional thinking effort. |
| `NINO_MODEL_TIMEOUT_SECONDS` | positive seconds | Model HTTP timeout; defaults to 150 so orchestration requests are not cut off by the old 60-second adapter limit. |
| `NINO_MCP_URL` | MCP endpoint | Defaults to `http://127.0.0.1:8091/mcp`. |
| `NINO_MCP_SERVERS` | JSON server array | Multi-MCP catalog; empty keeps the single-URL fallback. |
| `NINO_STORAGE_PATH` | SQLite file | Defaults to `nino-agent-storage/nino-agent.db`. |
| `NINO_MODEL_CONTEXT_TOKENS` | model context window | Defaults to 128K tokens. Set this to the selected model's real limit. |
| `NINO_CONTEXT_RESERVED_TOKENS` | non-history reserve | Defaults to 32K for instructions, tools, observations, and output. |
| `NINO_CONTEXT_RECENT_TOKENS` | recent history budget | Keep the newest 48K estimated tokens verbatim. |
| `NINO_CONTEXT_SUMMARY_TOKENS` | compacted history budget | Persist up to 12K estimated tokens of older context. |
| `NINO_LOOP_HARD_MAX_STEPS` | positive integer | Runtime ceiling for model decisions; default 8. |
| `NINO_LOOP_HARD_MAX_ACTIONS` | 1-100 | Runtime ceiling for planning and Tool actions; default 32. |
| `NINO_LOOP_HARD_TIMEOUT_SECONDS` | 1-3600 | Runtime Loop timeout ceiling; default 300. |
| `NINO_LOOP_HARD_MAX_CONSECUTIVE_FAILURES` | 1-20 | Runtime failure ceiling; default 3. |
| `NINO_LOOP_HARD_MAX_NO_PROGRESS_STEPS` | 1-20 | Runtime no-progress ceiling; default 3. |
| `NINO_GRAPH_MAX_PARALLEL_NODES` | positive integer | Global in-process Task Node concurrency ceiling. |

Recommended first live configuration uses the smallest dependency surface:

```bash
export NINO_RUNTIME_MODE=live
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=native
export OPENAI_API_KEY='<your-key>'
export INCERRY_OPENAI_BASE_URL='http://core.dns-pro.net:13001/v1'
export NINO_MCP_URL=http://127.0.0.1:8091/mcp
```

The Docker Compose profile fixes `NINO_RUNTIME_MODE=live`, `NINO_AGENT_ENGINE=lightweight`, and
`NINO_MODEL_ADAPTER=native`. It injects `OPENAI_API_KEY` from the host shell and fails Compose
validation when the key is absent. Demo mode remains available only for local tests or an explicitly
constructed non-Docker Runtime.

The Runtime ignores `NINO_MODEL_NAME`, `NINO_MODEL_API_KEY`, `NINO_MODEL_BASE_URL`,
`OPENAI_MODEL`, and `OPENAI_BASE_URL`. Do not place the API key in project files.

Multiple MCP servers:

```bash
export NINO_MCP_SERVERS='[{"id":"nino-data","url":"http://127.0.0.1:8091/mcp","required":true},{"id":"report","url":"http://127.0.0.1:8092/mcp","required":false}]'
```

Tool names must be globally unique. Required-server discovery failure blocks the catalog; optional
server failure is isolated. Skill allowlists and Agent role policies are applied after Registry discovery.

The Planner receives capability metadata, compact prior node outcomes, conversation history, and the
internal plan-node schema, never business MCP tools.
The Orchestrator owns accepted Graph state and receives no planning or business tools during final
reconciliation. After scheduling, a generic Analyst or Verifier receives only the selected Skill's
MCP schemas allowed by its read-only role policy.

## Persistent Follow-up Context

Every follow-up must reuse the same `conversation_id`. Conversations, user/assistant messages,
runs, events, and compacted context are stored in SQLite. The default local file is
`nino-agent-storage/nino-agent.db`; Docker mounts that folder at `/app/storage`.

History is passed in full until it reaches `NINO_MODEL_CONTEXT_TOKENS - NINO_CONTEXT_RESERVED_TOKENS`.
The Runtime then preserves the newest messages verbatim and stores a token-bounded extractive
summary with a `through_message_id` cursor. Later runs reuse that summary plus messages after the
cursor. They compact again only when this composed context exceeds the budget, and then advance
the cursor over newly compacted messages. Raw Conversation messages remain unchanged.

Run metadata distinguishes the behavior:

- `context.mode=full`: no summary exists and full history was used.
- `context.mode=compacted, compaction_performed=true`: this run created or advanced a summary.
- `context.mode=compacted, summary_reused=true, compaction_performed=false`: persisted summary was reused without compression.

Read the latest persisted compaction with:

```bash
curl -s http://127.0.0.1:8090/api/v1/conversations/{conversation_id}/context
```

## Loop Engineering

Orchestration and Worker ReAct use one deterministic `LoopController` contract. Agent and Skill
manifests declare action, timeout, consecutive-failure, and no-progress budgets; Worker values are
the strict minimum across both manifests. Every model boundary, Observation, and terminal state emits
`loop_checkpoint`, which is persisted in SQLite `run_events` and replayed through SSE.

```bash
curl -s http://127.0.0.1:8090/api/v1/runs/{run_id}/loop-checkpoint
curl -s 'http://127.0.0.1:8090/api/v1/runs/{run_id}/loop-checkpoint?kind=worker_react'
```

Snapshots contain counts, elapsed time, budgets, status, stop reason, and an Action hash. They never
contain raw Action arguments, credentials, hidden chain-of-thought, or persisted `reasoning_content`.
A Loop checkpoint is observation and diagnosis state, not a continuation of hidden model execution.
Runtime recovery instead replays the Root orchestration and reuses completed nodes whose persisted
identity and result remain stable. Exact continuation from a model/tool boundary, or direct resume from
an arbitrary persisted Ready Node, is not implemented. See
[Task-level Harness Complete Design](../../doc/task-level-harness-design.md#15-恢复语义).

Framework alternatives:

```bash
# LangChain model adapter with the controlled lightweight loop
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=langchain

# LangGraph model/tool state graph; either model adapter can be used
export NINO_AGENT_ENGINE=langgraph
export NINO_MODEL_ADAPTER=native
```

Start with native + lightweight to prove model tool-calling and business answers. LangGraph is an
optional implementation of the Worker model/tool state graph; it is not the durable macro TaskGraph
and selecting it does not by itself provide approval or exact crash resume. LangChain is useful for
provider integrations, but is not required by the core.

## Skills, References, and Agents

Every `SKILL.md` and `AGENT.md` starts with YAML frontmatter. Markdown owns the role `name` and
`description`; JSON manifests contain machine-enforced permissions and budgets.

```markdown
---
name: nino-data-analysis
description: |
  Read-only Nino Data analysis workflow. Use for order queries and statistics.
---
```

Skill references are declared in `skill.json` and loaded only through
`nino_runtime_load_reference`. The model supplies a reference ID, never a file path. Runtime
enforces directory containment, file existence, a character budget, and emits
`reference_loaded` with the document SHA256.

The four standard Agents are `nino.orchestrator`, `nino.planner`, `nino.analyst`, and
`nino.verifier`; all are business-neutral. They map to the code flow as follows:

| Agent | Owns | Must not do |
|---|---|---|
| `nino.orchestrator` | Route, validate proposals, emit accepted revisions, schedule, enforce Gates, reconcile final answer | Execute business MCP or delegate Graph Truth |
| `nino.planner` | Propose bounded candidate nodes, dependencies, bindings, and acceptance contracts | Persist, schedule, execute MCP, or answer the user |
| `nino.analyst` | Execute one selected read-only Skill and return evidence-grounded Node Result | Plan the root Run or claim independent verification |
| `nino.verifier` | Independently re-query minimal evidence and submit a structured verdict | Trust Analyst prose as proof or repair its claim |

Routing first performs
deterministic exclusion and keyword recall. Keyword matches directly constrain the candidate catalog;
when no keyword matches, only Skills with `semantic_fallback=true` may enter model-assisted routing.
The Planner proposes bounded TaskGraph nodes, clarification, or rejection. The Orchestrator validates
the proposal, emits the accepted Graph revision, schedules it, and remains the only Graph control plane.
Planning uses the internal
`nino_runtime_submit_task_graph_node(agent_id, skill_id, task, context, depends_on, input_bindings, acceptance_contract)`
Action. The selected generic Analyst or Verifier receives a fresh context, loads the chosen Skill and
References, and alone receives its approved MCP tools. Its effective Tool set is discovered MCP tools
intersected with `Skill.allowed_tools` and Agent role policy. Therefore a compatible new read-only
business normally adds a Skill, References, MCP integration, fixed question bank, and tests without
editing or cloning Agent manifests.
Child model/reference/tool events are folded into the parent Run with `parent_run_id`, `child_run_id`,
`agent_id`, and `skill_id`. A Worker may produce a factual final answer only after a successful Tool
Observation; missing input must use the validated `nino_runtime_request_clarification` Action rather
than plain assistant text. The demo phrase
`复杂统计 2026 年 7 月毛利并核对结论` exercises analyst followed by verifier. A general question can
enter the opt-in semantic fallback path and must still finish through a validated structured rejection;
it is not accurate to promise that every unmatched request is rejected before any model call.

## Tests

The current `0.15.0` code passes the complete Python unit suite. Run it after implementation changes:

```bash
.venv/bin/python -m unittest discover -s tests -v
```
