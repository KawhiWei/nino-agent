from __future__ import annotations

import json
import os
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Header, Query, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse

from version import __version__
from harness import AgentRegistry, SkillRegistry, lint_task_graph
from runtime import (
    AgentRuntimeService, ContextWindowConfig, ConversationContextManager,
    ResourceNotFoundError, RunConflictError,
)
from bootstrap import RuntimeSettings, build_harness_assembly
from infrastructure import SqliteAgentRepository
from infrastructure.mcp import McpServerRegistry
from framework import AgentEvent, AgentHarness, AgentRepository, AgentRun, RunStatus
from .schemas import (
    ConversationResponse,
    ContextResponse,
    CreateConversationRequest,
    CreateMessageRequest,
    ErrorBody,
    ErrorResponse,
    AgentResponse,
    EventListResponse,
    EventResponse,
    HealthResponse,
    LoopBudgetResponse,
    MessageResponse,
    McpServerResponse,
    RunAcceptedResponse,
    RunResponse,
    SkillResponse,
    TaskGraphResponse,
    HarnessLintResponse,
    TaskNodeResponse,
    TaskGateResponse,
    NodeAttemptResponse,
)


TERMINAL_STATUSES = {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}


class ApiContainer:
    def __init__(
        self, service: AgentRuntimeService, skills: SkillRegistry,
        agents: AgentRegistry, settings: RuntimeSettings,
        mcp_registry: McpServerRegistry | None = None,
    ) -> None:
        self.service = service
        self.skills = skills
        self.agents = agents
        self.settings = settings
        self.mcp_registry = mcp_registry


def _default_skill_path() -> Path:
    configured = os.getenv("NINO_SKILLS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "shared" / "skills"


def _default_agent_path() -> Path:
    configured = os.getenv("NINO_AGENTS_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[3] / "shared" / "agents"


def _default_storage_path() -> Path:
    configured = os.getenv("NINO_STORAGE_PATH")
    if configured:
        return Path(configured).expanduser().resolve()
    return Path(__file__).resolve().parents[4] / "nino-agent-storage" / "nino-agent.db"


def _error(status_code: int, code: str, message: str) -> JSONResponse:
    payload = ErrorResponse(error=ErrorBody(code=code, message=message))
    return JSONResponse(status_code=status_code, content=payload.model_dump())


def _event_payload(event: AgentEvent) -> EventResponse:
    return EventResponse.model_validate(event)


def _run_payload(run: AgentRun) -> RunResponse:
    return RunResponse.model_validate(run)


def create_app(
    *,
    harness: AgentHarness | None = None,
    repository: AgentRepository | None = None,
    skills: SkillRegistry | None = None,
    agents: AgentRegistry | None = None,
    runtime_mode: str | None = None,
) -> FastAPI:
    skills = skills or SkillRegistry.load(_default_skill_path())
    agents = agents or AgentRegistry.load(_default_agent_path())
    settings = RuntimeSettings.from_env()
    if runtime_mode is not None:
        settings = replace(settings, mode=runtime_mode)
    mcp_registry: McpServerRegistry | None = None
    if harness is None:
        assembly = build_harness_assembly(skills, agents, settings)
        harness = assembly.harness
        if isinstance(assembly.tools, McpServerRegistry):
            mcp_registry = assembly.tools
    repository = repository or SqliteAgentRepository(_default_storage_path())
    max_concurrent_runs = int(os.getenv("NINO_MAX_CONCURRENT_RUNS", "4"))
    model_context_tokens = int(os.getenv("NINO_MODEL_CONTEXT_TOKENS", "128000"))
    reserved_context_tokens = int(os.getenv("NINO_CONTEXT_RESERVED_TOKENS", "32000"))
    recent_context_tokens = int(os.getenv("NINO_CONTEXT_RECENT_TOKENS", "48000"))
    summary_context_tokens = int(os.getenv("NINO_CONTEXT_SUMMARY_TOKENS", "12000"))
    context_manager = ConversationContextManager(ContextWindowConfig(
        model_context_tokens=model_context_tokens,
        reserved_tokens=reserved_context_tokens,
        recent_tokens=recent_context_tokens,
        summary_tokens=summary_context_tokens,
    ))
    container = ApiContainer(
        AgentRuntimeService(
            harness, repository, max_concurrent_runs, context_manager=context_manager
        ),
        skills, agents, settings, mcp_registry
    )

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        await container.service.start()
        try:
            yield
        finally:
            await container.service.shutdown()
            if container.mcp_registry is not None:
                await container.mcp_registry.close()

    app = FastAPI(
        title="Nino Agent Runtime API",
        version=__version__,
        description="API-first Python enterprise ReAct Harness for App, Web, and Desktop.",
        lifespan=lifespan,
    )
    app.state.container = container

    origins = [
        origin.strip()
        for origin in os.getenv(
            "NINO_CORS_ORIGINS", "http://localhost:3000,http://localhost:5173"
        ).split(",")
        if origin.strip()
    ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", "Authorization", "Last-Event-ID"],
    )

    @app.exception_handler(ResourceNotFoundError)
    async def not_found_handler(_: Request, exc: ResourceNotFoundError) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, "RESOURCE_NOT_FOUND", str(exc))

    @app.exception_handler(RunConflictError)
    async def conflict_handler(_: Request, exc: RunConflictError) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, "RUN_CONFLICT", str(exc))

    @app.exception_handler(RequestValidationError)
    async def validation_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        return _error(status.HTTP_422_UNPROCESSABLE_ENTITY, "VALIDATION_ERROR", str(exc))

    @app.exception_handler(ValueError)
    async def value_error_handler(_: Request, exc: ValueError) -> JSONResponse:
        return _error(status.HTTP_400_BAD_REQUEST, "INVALID_ARGUMENT", str(exc))

    @app.get("/health", response_model=HealthResponse, tags=["system"])
    async def health() -> HealthResponse:
        return HealthResponse(
            status="ok",
            service="nino-agent-runtime",
            version=__version__,
            runtime_mode=settings.mode,
            agent_engine=settings.engine,
            model_adapter="demo" if settings.mode == "demo" else settings.model_adapter,
        )

    @app.get("/api/v1/skills", response_model=list[SkillResponse], tags=["skills"])
    async def list_skills() -> list[SkillResponse]:
        return [
            SkillResponse(
                id=skill.id,
                name=skill.name,
                version=skill.version,
                description=skill.description,
                allowed_tools=sorted(skill.allowed_tools),
                references=[item.id for item in skill.references],
                max_steps=skill.max_steps,
                capabilities=list(skill.capabilities),
                risk_level=skill.risk_level,
                required_evaluators=list(skill.required_evaluators),
                semantic_routing=skill.semantic_routing,
                workflow_id=skill.workflow_id,
                workflow_execution_shape=skill.workflow_execution_shape,
                assurance_mode=skill.assurance_mode,
                loop=LoopBudgetResponse(
                    max_actions=skill.loop_budget.max_actions,
                    timeout_seconds=skill.loop_budget.timeout_seconds,
                    max_consecutive_failures=skill.loop_budget.max_consecutive_failures,
                    max_no_progress_steps=skill.loop_budget.max_no_progress_steps,
                ),
            )
            for skill in skills.skills
        ]

    @app.get("/api/v1/agents", response_model=list[AgentResponse], tags=["agents"])
    async def list_agents() -> list[AgentResponse]:
        return [
            AgentResponse(
                id=agent.id,
                name=agent.name,
                description=agent.description,
                mode=agent.mode,
                role=agent.role,
                evaluator_kind=agent.evaluator_kind,
                allowed_skills=sorted(agent.allowed_skills),
                allowed_tools=sorted(agent.allowed_tools),
                allowed_delegates=sorted(agent.allowed_delegates),
                capabilities=list(agent.capabilities),
                discover_delegates=agent.discover_delegates,
                max_steps=agent.max_steps,
                max_delegation_depth=agent.max_delegation_depth,
                loop=LoopBudgetResponse(
                    max_actions=agent.loop_budget.max_actions,
                    timeout_seconds=agent.loop_budget.timeout_seconds,
                    max_consecutive_failures=agent.loop_budget.max_consecutive_failures,
                    max_no_progress_steps=agent.loop_budget.max_no_progress_steps,
                ),
            )
            for agent in agents.agents
        ]

    @app.get("/api/v1/mcp/servers", response_model=list[McpServerResponse], tags=["mcp"])
    async def list_mcp_servers(
        discover: bool = Query(default=False),
    ) -> list[McpServerResponse]:
        registry = container.mcp_registry
        if registry is None:
            return []
        if discover:
            await registry.list_tools()
        return [McpServerResponse(
            id=item.id,
            required=item.required,
            state=item.state,
            tool_count=item.tool_count,
            error=item.error,
            consecutive_failures=item.consecutive_failures,
        ) for item in registry.statuses]

    @app.post(
        "/api/v1/conversations",
        response_model=ConversationResponse,
        status_code=status.HTTP_201_CREATED,
        tags=["conversations"],
    )
    async def create_conversation(body: CreateConversationRequest) -> ConversationResponse:
        conversation = await container.service.create_conversation(body.title)
        return ConversationResponse.model_validate(conversation)

    @app.get(
        "/api/v1/conversations",
        response_model=list[ConversationResponse],
        tags=["conversations"],
    )
    async def list_conversations(
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[ConversationResponse]:
        conversations = await container.service.list_conversations()
        return [
            ConversationResponse.model_validate(item)
            for item in conversations[offset:offset + limit]
        ]

    @app.get(
        "/api/v1/conversations/{conversation_id}",
        response_model=ConversationResponse,
        tags=["conversations"],
    )
    async def get_conversation(conversation_id: str) -> ConversationResponse:
        conversation = await container.service.get_conversation(conversation_id)
        return ConversationResponse.model_validate(conversation)

    @app.get(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=list[MessageResponse],
        tags=["conversations"],
    )
    async def list_messages(conversation_id: str) -> list[MessageResponse]:
        messages = await container.service.list_messages(conversation_id)
        return [MessageResponse.model_validate(message) for message in messages]

    @app.get(
        "/api/v1/conversations/{conversation_id}/context",
        response_model=ContextResponse | None,
        tags=["conversations"],
    )
    async def get_conversation_context(conversation_id: str) -> ContextResponse | None:
        context = await container.service.get_context(conversation_id)
        return ContextResponse.model_validate(context) if context is not None else None

    @app.get(
        "/api/v1/conversations/{conversation_id}/runs",
        response_model=list[RunResponse],
        tags=["runs"],
    )
    async def list_conversation_runs(
        conversation_id: str,
        offset: int = Query(default=0, ge=0),
        limit: int = Query(default=50, ge=1, le=200),
    ) -> list[RunResponse]:
        runs = await container.service.list_runs(conversation_id)
        return [_run_payload(run) for run in runs[offset:offset + limit]]

    @app.post(
        "/api/v1/conversations/{conversation_id}/messages",
        response_model=RunAcceptedResponse,
        status_code=status.HTTP_202_ACCEPTED,
        tags=["runs"],
    )
    async def submit_message(
        conversation_id: str, body: CreateMessageRequest
    ) -> RunAcceptedResponse:
        run = await container.service.submit_message(conversation_id, body.content)
        return RunAcceptedResponse.model_validate(run)

    @app.get("/api/v1/runs/{run_id}", response_model=RunResponse, tags=["runs"])
    async def get_run(run_id: str) -> RunResponse:
        return _run_payload(await container.service.get_run(run_id))

    @app.get(
        "/api/v1/runs/{run_id}/task-graph",
        response_model=TaskGraphResponse,
        tags=["harness"],
    )
    async def get_run_task_graph(run_id: str) -> TaskGraphResponse:
        snapshot = await container.service.get_task_graph(run_id)
        return TaskGraphResponse.model_validate({
            "graph": snapshot.graph,
            "nodes": list(snapshot.nodes),
            "gates": list(snapshot.gates),
            "attempts": list(snapshot.attempts),
        })

    @app.get(
        "/api/v1/runs/{run_id}/task-graph/lint",
        response_model=HarnessLintResponse,
        tags=["harness"],
    )
    async def lint_run_task_graph(run_id: str) -> HarnessLintResponse:
        issues = lint_task_graph(await container.service.get_task_graph(run_id))
        return HarnessLintResponse.model_validate({
            "valid": not issues,
            "issues": [
                {"code": item.code, "message": item.message, "node_id": item.node_id}
                for item in issues
            ],
        })

    @app.get(
        "/api/v1/runs/{run_id}/task-graph/nodes",
        response_model=list[TaskNodeResponse], tags=["harness"],
    )
    async def list_run_task_nodes(run_id: str) -> list[TaskNodeResponse]:
        snapshot = await container.service.get_task_graph(run_id)
        return [TaskNodeResponse.model_validate(item) for item in snapshot.nodes]

    @app.get(
        "/api/v1/runs/{run_id}/task-graph/gates",
        response_model=list[TaskGateResponse], tags=["harness"],
    )
    async def list_run_task_gates(run_id: str) -> list[TaskGateResponse]:
        snapshot = await container.service.get_task_graph(run_id)
        return [TaskGateResponse.model_validate(item) for item in snapshot.gates]

    @app.get(
        "/api/v1/runs/{run_id}/task-graph/attempts",
        response_model=list[NodeAttemptResponse], tags=["harness"],
    )
    async def list_run_node_attempts(run_id: str) -> list[NodeAttemptResponse]:
        snapshot = await container.service.get_task_graph(run_id)
        return [NodeAttemptResponse.model_validate(item) for item in snapshot.attempts]

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunResponse, tags=["runs"])
    async def cancel_run(run_id: str) -> RunResponse:
        return _run_payload(await container.service.cancel_run(run_id))

    @app.get(
        "/api/v1/runs/{run_id}/events",
        response_model=EventListResponse,
        tags=["runs"],
    )
    async def list_run_events(
        run_id: str, after: int = Query(default=0, ge=0)
    ) -> EventListResponse:
        events = await container.service.list_events(run_id, after)
        payloads = [_event_payload(event) for event in events]
        return EventListResponse(
            events=payloads,
            next_after=payloads[-1].sequence if payloads else after,
        )

    @app.get(
        "/api/v1/runs/{run_id}/loop-checkpoint",
        response_model=EventResponse | None,
        tags=["runs"],
    )
    async def get_latest_loop_checkpoint(
        run_id: str,
        kind: str | None = Query(default=None, pattern="^(orchestration|worker_react)$"),
    ) -> EventResponse | None:
        event = await container.service.get_latest_loop_checkpoint(run_id, kind)
        return _event_payload(event) if event is not None else None

    @app.get(
        "/api/v1/runs/{run_id}/events/stream",
        response_class=StreamingResponse,
        tags=["runs"],
    )
    async def stream_run_events(
        run_id: str,
        after: int = Query(default=0, ge=0),
        last_event_id: str | None = Header(default=None, alias="Last-Event-ID"),
    ) -> StreamingResponse:
        await container.service.get_run(run_id)
        cursor = max(after, int(last_event_id)) if last_event_id and last_event_id.isdigit() else after

        async def generate() -> AsyncIterator[str]:
            nonlocal cursor
            while True:
                events = await container.service.wait_for_events(run_id, cursor, 15.0)
                for event in events:
                    cursor = event.sequence
                    data = json.dumps(_event_payload(event).model_dump(), ensure_ascii=False)
                    yield f"id: {event.sequence}\nevent: {event.type}\ndata: {data}\n\n"
                run = await container.service.get_run(run_id)
                if run.status in TERMINAL_STATUSES:
                    break
                if not events:
                    yield ": keep-alive\n\n"

        return StreamingResponse(
            generate(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "X-Accel-Buffering": "no",
                "Connection": "keep-alive",
            },
        )

    return app


app = create_app()
