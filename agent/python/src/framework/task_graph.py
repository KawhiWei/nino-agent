from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from hashlib import sha256
import json
from typing import Any, Mapping

from .conversation import utc_now


class TaskGraphStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskNodeStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    AWAITING_APPROVAL = "awaiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    SUPERSEDED = "superseded"


class GateStatus(StrEnum):
    PENDING = "pending"
    PASSED = "passed"
    FAILED = "failed"
    BLOCKED = "blocked"


class AttemptStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass(frozen=True, slots=True)
class AcceptanceContract:
    """Stable definition of what a node may claim after its gate passes."""

    spec_source: str
    target_outcome: str
    positive_checks: tuple[str, ...]
    negative_checks: tuple[str, ...] = ()
    evidence_requirements: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    pass_label: str = "verified"

    def to_data(self) -> Mapping[str, Any]:
        return {
            "spec_source": self.spec_source,
            "target_outcome": self.target_outcome,
            "positive_checks": list(self.positive_checks),
            "negative_checks": list(self.negative_checks),
            "evidence_requirements": list(self.evidence_requirements),
            "gaps": list(self.gaps),
            "pass_label": self.pass_label,
        }

    @classmethod
    def from_data(cls, value: Mapping[str, Any]) -> "AcceptanceContract":
        return cls(
            spec_source=str(value.get("spec_source", "")),
            target_outcome=str(value.get("target_outcome", "")),
            positive_checks=tuple(str(item) for item in value.get("positive_checks", ())),
            negative_checks=tuple(str(item) for item in value.get("negative_checks", ())),
            evidence_requirements=tuple(
                str(item) for item in value.get("evidence_requirements", ())
            ),
            gaps=tuple(str(item) for item in value.get("gaps", ())),
            pass_label=str(value.get("pass_label", "verified")),
        )


@dataclass(slots=True)
class TaskGraph:
    id: str
    run_id: str
    conversation_id: str
    user_intent: str
    status: TaskGraphStatus = TaskGraphStatus.PENDING
    version: int = 1
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    parent_graph_id: str | None = None
    relation_type: str | None = None
    archived_at: datetime | None = None


@dataclass(slots=True)
class TaskNode:
    id: str
    graph_id: str
    kind: str
    owner_agent_id: str
    title: str
    contract: AcceptanceContract
    status: TaskNodeStatus = TaskNodeStatus.PENDING
    parent_node_id: str | None = None
    dependencies: tuple[str, ...] = ()
    result_summary: str = ""
    result: Mapping[str, Any] = field(default_factory=dict)
    error_code: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(slots=True)
class TaskGate:
    id: str
    graph_id: str
    node_id: str
    kind: str
    status: GateStatus = GateStatus.PENDING
    required: bool = True
    evaluator_agent_id: str | None = None
    verdict: str = ""
    evidence: tuple[str, ...] = ()
    created_at: datetime = field(default_factory=utc_now)
    evaluated_at: datetime | None = None


@dataclass(slots=True)
class NodeAttempt:
    id: str
    graph_id: str
    node_id: str
    attempt_number: int
    status: AttemptStatus = AttemptStatus.RUNNING
    lease_owner: str | None = None
    lease_expires_at: datetime | None = None
    started_at: datetime = field(default_factory=utc_now)
    completed_at: datetime | None = None
    error_code: str | None = None
    checkpoint: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class TaskGraphSnapshot:
    graph: TaskGraph
    nodes: tuple[TaskNode, ...]
    gates: tuple[TaskGate, ...]
    attempts: tuple[NodeAttempt, ...]


def task_node_fingerprint(payload: Mapping[str, Any]) -> str:
    """Return the stable identity of one executable node contract."""

    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return sha256(encoded).hexdigest()
