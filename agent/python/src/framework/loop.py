from __future__ import annotations

from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any, Mapping


class LoopKind(StrEnum):
    ORCHESTRATION = "orchestration"
    WORKER_REACT = "worker_react"


class LoopStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class LoopStopReason(StrEnum):
    FINAL_ANSWER = "final_answer"
    MAX_STEPS = "max_steps"
    MAX_ACTIONS = "max_actions"
    TIMEOUT = "timeout"
    DUPLICATE_ACTION = "duplicate_action"
    CONSECUTIVE_FAILURES = "consecutive_failures"
    NO_PROGRESS = "no_progress"
    POLICY_VIOLATION = "policy_violation"
    DEPENDENCY_ERROR = "dependency_error"
    CANCELLED = "cancelled"


@dataclass(frozen=True, slots=True)
class LoopBudget:
    max_actions: int = 16
    timeout_seconds: int = 120
    max_consecutive_failures: int = 2
    max_no_progress_steps: int = 2

    def __post_init__(self) -> None:
        if not 1 <= self.max_actions <= 100:
            raise ValueError("Loop max_actions must be between 1 and 100.")
        if not 1 <= self.timeout_seconds <= 3600:
            raise ValueError("Loop timeout_seconds must be between 1 and 3600.")
        if not 1 <= self.max_consecutive_failures <= 20:
            raise ValueError("Loop max_consecutive_failures must be between 1 and 20.")
        if not 1 <= self.max_no_progress_steps <= 20:
            raise ValueError("Loop max_no_progress_steps must be between 1 and 20.")


@dataclass(frozen=True, slots=True)
class LoopSnapshot:
    run_id: str
    kind: LoopKind
    status: LoopStatus
    step: int
    max_steps: int
    action_count: int
    max_actions: int
    successful_actions: int
    failed_actions: int
    consecutive_failures: int
    no_progress_steps: int
    elapsed_ms: int
    timeout_seconds: int
    last_action_hash: str | None = None
    stop_reason: LoopStopReason | None = None
    error_code: str | None = None

    def to_data(self) -> Mapping[str, Any]:
        payload = asdict(self)
        payload["kind"] = self.kind.value
        payload["status"] = self.status.value
        payload["stop_reason"] = self.stop_reason.value if self.stop_reason else None
        return payload

