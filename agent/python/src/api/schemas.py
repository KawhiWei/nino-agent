from __future__ import annotations

from datetime import datetime
from typing import Any, Mapping

from pydantic import BaseModel, ConfigDict, Field


class ApiModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


class CreateConversationRequest(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class ConversationResponse(ApiModel):
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


class CreateMessageRequest(BaseModel):
    content: str = Field(min_length=1, max_length=20_000)


class MessageResponse(ApiModel):
    id: str
    conversation_id: str
    role: str
    content: str
    run_id: str | None
    created_at: datetime


class ContextResponse(ApiModel):
    conversation_id: str
    summary: str
    through_message_id: str
    compacted_message_count: int
    original_tokens: int
    updated_at: datetime
    algorithm_version: str
    token_counter: str


class RunAcceptedResponse(ApiModel):
    run_id: str = Field(validation_alias="id")
    conversation_id: str
    status: str


class RunResponse(ApiModel):
    id: str
    conversation_id: str
    status: str
    skill_id: str | None
    answer: str
    error_code: str | None
    steps: int
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    metadata: Mapping[str, Any]


class AcceptanceContractResponse(ApiModel):
    spec_source: str
    target_outcome: str
    positive_checks: list[str] | tuple[str, ...]
    negative_checks: list[str] | tuple[str, ...]
    evidence_requirements: list[str] | tuple[str, ...]
    gaps: list[str] | tuple[str, ...]
    pass_label: str


class TaskNodeResponse(ApiModel):
    id: str
    graph_id: str
    kind: str
    owner_agent_id: str
    title: str
    status: str
    parent_node_id: str | None
    dependencies: list[str] | tuple[str, ...]
    contract: AcceptanceContractResponse
    result_summary: str
    result: Mapping[str, Any]
    error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    metadata: Mapping[str, Any]


class TaskGateResponse(ApiModel):
    id: str
    graph_id: str
    node_id: str
    kind: str
    status: str
    required: bool
    evaluator_agent_id: str | None
    verdict: str
    evidence: list[str] | tuple[str, ...]
    created_at: datetime
    evaluated_at: datetime | None


class NodeAttemptResponse(ApiModel):
    id: str
    graph_id: str
    node_id: str
    attempt_number: int
    status: str
    lease_owner: str | None
    lease_expires_at: datetime | None
    started_at: datetime
    completed_at: datetime | None
    error_code: str | None
    checkpoint: Mapping[str, Any]


class TaskGraphDetailResponse(ApiModel):
    id: str
    run_id: str
    conversation_id: str
    user_intent: str
    status: str
    version: int
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None
    metadata: Mapping[str, Any]
    parent_graph_id: str | None
    relation_type: str | None
    archived_at: datetime | None


class TaskGraphResponse(BaseModel):
    graph: TaskGraphDetailResponse
    nodes: list[TaskNodeResponse]
    gates: list[TaskGateResponse]
    attempts: list[NodeAttemptResponse]


class HarnessLintIssueResponse(BaseModel):
    code: str
    message: str
    node_id: str | None


class HarnessLintResponse(BaseModel):
    valid: bool
    issues: list[HarnessLintIssueResponse]


class EventResponse(ApiModel):
    run_id: str
    sequence: int
    type: str
    data: Mapping[str, Any]


class EventListResponse(BaseModel):
    events: list[EventResponse]
    next_after: int


class LoopBudgetResponse(BaseModel):
    max_actions: int
    timeout_seconds: int
    max_consecutive_failures: int
    max_no_progress_steps: int


class SkillResponse(BaseModel):
    id: str
    name: str
    version: str
    description: str
    allowed_tools: list[str]
    references: list[str]
    max_steps: int
    capabilities: list[str]
    risk_level: str
    required_evaluators: list[str]
    semantic_routing: bool
    workflow_id: str
    workflow_execution_shape: str
    assurance_mode: str
    loop: LoopBudgetResponse


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    mode: str
    role: str
    evaluator_kind: str | None
    allowed_skills: list[str]
    allowed_tools: list[str]
    allowed_delegates: list[str]
    capabilities: list[str]
    discover_delegates: bool
    max_steps: int
    max_delegation_depth: int
    loop: LoopBudgetResponse


class McpServerResponse(BaseModel):
    id: str
    required: bool
    state: str
    tool_count: int
    error: str | None
    consecutive_failures: int


class HealthResponse(BaseModel):
    status: str
    service: str
    version: str
    runtime_mode: str
    agent_engine: str
    model_adapter: str


class ErrorBody(BaseModel):
    code: str
    message: str


class ErrorResponse(BaseModel):
    error: ErrorBody
