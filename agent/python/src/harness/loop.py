from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass

from framework import (
    LoopBudget, LoopKind, LoopSnapshot, LoopStatus, LoopStopReason,
)


@dataclass(frozen=True, slots=True)
class LoopViolation:
    error_code: str
    message: str
    stop_reason: LoopStopReason


class LoopController:
    """Deterministic budgets, progress accounting, and checkpoint snapshots for one loop."""

    def __init__(
        self,
        run_id: str,
        kind: LoopKind,
        max_steps: int,
        budget: LoopBudget | None = None,
    ) -> None:
        if max_steps < 1:
            raise ValueError("Loop max_steps must be positive.")
        self.run_id = run_id
        self.kind = kind
        self.max_steps = max_steps
        self.budget = budget or LoopBudget()
        self.step = 0
        self.action_count = 0
        self.successful_actions = 0
        self.failed_actions = 0
        self.consecutive_failures = 0
        self.no_progress_steps = 0
        self.status = LoopStatus.RUNNING
        self.stop_reason: LoopStopReason | None = None
        self.error_code: str | None = None
        self._started = time.monotonic()
        self._signatures: set[str] = set()
        self._last_action_hash: str | None = None

    def begin_step(self) -> LoopViolation | None:
        violation = self.check_limits()
        if violation is not None:
            return violation
        if self.step >= self.max_steps:
            return LoopViolation(
                "MAX_STEPS_EXCEEDED",
                f"Loop exceeded the maximum of {self.max_steps} steps.",
                LoopStopReason.MAX_STEPS,
            )
        self.step += 1
        return None

    def register_action(self, signature: str) -> LoopViolation | None:
        violation = self.check_limits()
        if violation is not None:
            return violation
        if self.action_count >= self.budget.max_actions:
            return LoopViolation(
                "MAX_ACTIONS_EXCEEDED",
                f"Loop exceeded the maximum of {self.budget.max_actions} actions.",
                LoopStopReason.MAX_ACTIONS,
            )
        if signature in self._signatures:
            return LoopViolation(
                "DUPLICATE_ACTION",
                "The same action with identical arguments was already executed in this loop.",
                LoopStopReason.DUPLICATE_ACTION,
            )
        self._signatures.add(signature)
        self.action_count += 1
        self._last_action_hash = hashlib.sha256(signature.encode("utf-8")).hexdigest()
        return None

    def record_observation(self, succeeded: bool) -> LoopViolation | None:
        if succeeded:
            self.successful_actions += 1
            self.consecutive_failures = 0
            self.no_progress_steps = 0
            return self.check_limits()
        self.failed_actions += 1
        self.consecutive_failures += 1
        self.no_progress_steps += 1
        if self.consecutive_failures >= self.budget.max_consecutive_failures:
            return LoopViolation(
                "LOOP_CONSECUTIVE_FAILURES",
                "Loop stopped after repeated action failures.",
                LoopStopReason.CONSECUTIVE_FAILURES,
            )
        if self.no_progress_steps >= self.budget.max_no_progress_steps:
            return LoopViolation(
                "LOOP_NO_PROGRESS",
                "Loop stopped because repeated observations made no progress.",
                LoopStopReason.NO_PROGRESS,
            )
        return self.check_limits()

    def check_limits(self) -> LoopViolation | None:
        if self.elapsed_ms >= self.budget.timeout_seconds * 1000:
            return LoopViolation(
                "LOOP_TIMEOUT",
                f"Loop exceeded its {self.budget.timeout_seconds} second timeout.",
                LoopStopReason.TIMEOUT,
            )
        return None

    def stop(
        self,
        status: LoopStatus,
        reason: LoopStopReason,
        error_code: str | None = None,
    ) -> None:
        self.status = status
        self.stop_reason = reason
        self.error_code = error_code

    @property
    def elapsed_ms(self) -> int:
        return max(0, int((time.monotonic() - self._started) * 1000))

    @property
    def remaining_seconds(self) -> float:
        return max(0.001, self.budget.timeout_seconds - (self.elapsed_ms / 1000))

    def snapshot(self) -> LoopSnapshot:
        return LoopSnapshot(
            run_id=self.run_id,
            kind=self.kind,
            status=self.status,
            step=self.step,
            max_steps=self.max_steps,
            action_count=self.action_count,
            max_actions=self.budget.max_actions,
            successful_actions=self.successful_actions,
            failed_actions=self.failed_actions,
            consecutive_failures=self.consecutive_failures,
            no_progress_steps=self.no_progress_steps,
            elapsed_ms=self.elapsed_ms,
            timeout_seconds=self.budget.timeout_seconds,
            last_action_hash=self._last_action_hash,
            stop_reason=self.stop_reason,
            error_code=self.error_code,
        )


def strictest_budget(*budgets: LoopBudget) -> LoopBudget:
    """Compose policy layers so a lower layer may tighten but never widen an upper limit."""

    if not budgets:
        return LoopBudget()
    return LoopBudget(
        max_actions=min(item.max_actions for item in budgets),
        timeout_seconds=min(item.timeout_seconds for item in budgets),
        max_consecutive_failures=min(item.max_consecutive_failures for item in budgets),
        max_no_progress_steps=min(item.max_no_progress_steps for item in budgets),
    )


def stop_reason_for_error(error_code: str) -> LoopStopReason:
    if error_code == "MAX_STEPS_EXCEEDED":
        return LoopStopReason.MAX_STEPS
    if error_code == "MAX_ACTIONS_EXCEEDED":
        return LoopStopReason.MAX_ACTIONS
    if error_code == "LOOP_TIMEOUT":
        return LoopStopReason.TIMEOUT
    if error_code in {"DUPLICATE_ACTION", "DUPLICATE_TOOL_CALL"}:
        return LoopStopReason.DUPLICATE_ACTION
    if error_code == "LOOP_CONSECUTIVE_FAILURES":
        return LoopStopReason.CONSECUTIVE_FAILURES
    if error_code in {"LOOP_NO_PROGRESS", "EMPTY_MODEL_RESPONSE"}:
        return LoopStopReason.NO_PROGRESS
    if error_code in {"DEPENDENCY_ERROR", "TOOL_DISCOVERY_ERROR"}:
        return LoopStopReason.DEPENDENCY_ERROR
    if error_code == "RUN_CANCELLED":
        return LoopStopReason.CANCELLED
    return LoopStopReason.POLICY_VIOLATION
