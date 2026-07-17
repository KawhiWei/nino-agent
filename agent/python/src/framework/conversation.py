from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Mapping

from .models import RunStatus


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True, slots=True)
class Conversation:
    id: str
    title: str | None
    created_at: datetime
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    id: str
    conversation_id: str
    role: str
    content: str
    run_id: str | None
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ConversationContext:
    conversation_id: str
    summary: str
    through_message_id: str
    compacted_message_count: int
    original_tokens: int
    updated_at: datetime
    algorithm_version: str = "extractive-v1"
    token_counter: str = "approximate-v1"

@dataclass(slots=True)
class AgentRun:
    id: str
    conversation_id: str
    status: RunStatus = RunStatus.QUEUED
    skill_id: str | None = None
    answer: str = ""
    error_code: str | None = None
    steps: int = 0
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
