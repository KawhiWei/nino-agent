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
    loop: LoopBudgetResponse


class AgentResponse(BaseModel):
    id: str
    name: str
    description: str
    mode: str
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
