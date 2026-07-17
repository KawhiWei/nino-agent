# Python Agent Runtime API v0.13

API-first Python ReAct Runtime for App, Web, Desktop, and future ACP adapters. CLI is not a product entry point.

## Architecture

```text
FastAPI REST + SSE Host
    -> AgentRuntimeService
        -> AgentHarness port
            -> OrchestratorHarness (business-neutral control plane)
            -> dynamic Agent + Skill capability catalog
            -> ReActHarness (lightweight) or LangGraphReActHarness worker
            -> specialist run(...) -> repeated step(HarnessStepState)
            -> AgentRegistry (generic primary + discovered specialists)
            -> ReferenceProvider (approved on-demand context)
            -> ChatModel port (native OpenAI-compatible or LangChain)
            -> ToolProvider port
                -> McpServerRegistry
                    -> one McpHttpToolClient per configured server
            -> shared SkillRegistry
        -> AgentRepository port
            -> SqliteAgentRepository (default, persistent)
            -> InMemoryAgentRepository (tests/explicit injection)
        -> ConversationContextManager
            -> full history or persisted compacted summary + recent turns
```

Runtime 管理 Conversation、Run、上下文、取消和事件；Harness 管理 Prompt、Skill/Agent 策略和
ReAct 循环。Harness 不选择 MCP transport，而是调用 Framework `ToolProvider` Port。live 模式下
`McpServerRegistry` 聚合并路由多个 MCP Streamable HTTP Server。

Orchestrator 的路由先执行确定性排除和关键词召回；未命中时只允许显式 opt-in Skill 参与受控语义
判定。Graph 的 `depends_on` 表达控制依赖，`input_bindings` 表达结构化结果传递。每个 dispatch 的
Acceptance Contract 同时进入 Worker、Evaluator 和持久化 TaskNode。

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
│   │   ├── orchestrator.py        # Generic capability routing and structured dispatch
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
Generic control-plane routing and extension rules: [Generic Orchestrator Design](../../doc/generic-orchestrator-design.md).

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
| `GET` | `/api/v1/agents` | Discover primary and specialist Agents |
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
| `NINO_MCP_URL` | MCP endpoint | Defaults to `http://127.0.0.1:8091/mcp`. |
| `NINO_MCP_SERVERS` | JSON server array | Multi-MCP catalog; empty keeps the single-URL fallback. |
| `NINO_STORAGE_PATH` | SQLite file | Defaults to `nino-agent-storage/nino-agent.db`. |
| `NINO_MODEL_CONTEXT_TOKENS` | model context window | Defaults to 128K tokens. Set this to the selected model's real limit. |
| `NINO_CONTEXT_RESERVED_TOKENS` | non-history reserve | Defaults to 32K for instructions, tools, observations, and output. |
| `NINO_CONTEXT_RECENT_TOKENS` | recent history budget | Keep the newest 48K estimated tokens verbatim. |
| `NINO_CONTEXT_SUMMARY_TOKENS` | compacted history budget | Persist up to 12K estimated tokens of older context. |
| `NINO_LOOP_HARD_MAX_STEPS` | positive integer | Runtime ceiling for model decisions; default 8. |
| `NINO_LOOP_HARD_MAX_ACTIONS` | 1-100 | Runtime ceiling for Tool/dispatch actions; default 32. |
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

The Runtime ignores `NINO_MODEL_NAME`, `NINO_MODEL_API_KEY`, `NINO_MODEL_BASE_URL`,
`OPENAI_MODEL`, and `OPENAI_BASE_URL`. Do not place the API key in project files.

Multiple MCP servers:

```bash
export NINO_MCP_SERVERS='[{"id":"nino-data","url":"http://127.0.0.1:8091/mcp","required":true},{"id":"report","url":"http://127.0.0.1:8092/mcp","required":false}]'
```

Tool names must be globally unique. Required-server discovery failure blocks the catalog; optional
server failure is isolated. Agent and Skill allowlists are applied after Registry discovery.

The primary model receives capability metadata and the internal dispatch schema, never business MCP
tools. After dispatch, the specialist model receives only Skill-and-Agent-approved MCP schemas. Both
loops retain step budgets, allowlists, duplicate-call protection, cancellation, and result-size limits.

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
Checkpoint persistence currently supports progress, audit, and diagnosis; automatic crash resume is
not yet implemented. See [Loop Engineering Design](../../doc/loop-engineering-design.md).

Framework alternatives:

```bash
# LangChain model adapter with the controlled lightweight loop
export NINO_AGENT_ENGINE=lightweight
export NINO_MODEL_ADAPTER=langchain

# LangGraph model/tool state graph; either model adapter can be used
export NINO_AGENT_ENGINE=langgraph
export NINO_MODEL_ADAPTER=native
```

Start with native + lightweight to prove model tool-calling and business answers. Switch to
LangGraph when checkpointing, branching, approval nodes, or resumable workflows become real
requirements. LangChain is useful for its provider integrations, but is not required by the core.

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

The primary `nino.orchestrator` is business-neutral and strict-scope. Input that does not match a
registered Skill is rejected before a model call. Matched work must use the internal
`nino_runtime_dispatch_agent(agent_id, skill_id, task, context)` tool. The selected specialist receives
a fresh context, loads the chosen Skill and References, and alone receives its approved MCP tools.
Child model/reference/tool events are folded into the parent Run with `parent_run_id`, `child_run_id`,
`agent_id`, and `skill_id`. A Worker may produce a factual final answer only after a successful Tool
Observation; missing input must use the validated `nino_runtime_request_clarification` Action rather
than plain assistant text. The demo phrase
`复杂统计 2026 年 7 月毛利并核对结论` exercises analyst followed by verifier; a general question
exercises deterministic `OUT_OF_SCOPE` rejection without calling the model.

## Tests

```bash
.venv/bin/python -m unittest discover -s tests -v
```
