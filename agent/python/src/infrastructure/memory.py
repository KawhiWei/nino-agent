from __future__ import annotations

import asyncio
from dataclasses import replace
from datetime import timedelta
from typing import Sequence
from uuid import uuid4

from framework import (
    ActiveRunConflictError, AgentEvent, AgentRun, AttemptStatus, Conversation,
    ConversationContext, ConversationMessage, GateStatus, NodeAttempt, TaskGate,
    RunStatus, TaskGraph, TaskGraphSnapshot, TaskGraphStatus, TaskNode, TaskNodeStatus,
    utc_now,
)


class InMemoryAgentRepository:
    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[ConversationMessage]] = {}
        self._runs: dict[str, AgentRun] = {}
        self._events: dict[str, list[AgentEvent]] = {}
        self._contexts: dict[str, ConversationContext] = {}
        self._event_conditions: dict[str, asyncio.Condition] = {}
        self._graphs: dict[str, TaskGraph] = {}
        self._graph_run_ids: dict[str, str] = {}
        self._nodes: dict[str, TaskNode] = {}
        self._gates: dict[str, TaskGate] = {}
        self._attempts: dict[str, NodeAttempt] = {}
        self._lock = asyncio.Lock()
        self._runtimes: dict[str, str] = {}

    async def create_conversation(self, conversation: Conversation) -> None:
        async with self._lock:
            self._conversations[conversation.id] = conversation
            self._messages[conversation.id] = []

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self._lock:
            return self._conversations.get(conversation_id)

    async def list_conversations(self) -> Sequence[Conversation]:
        async with self._lock:
            return tuple(sorted(
                self._conversations.values(), key=lambda item: item.updated_at, reverse=True
            ))

    async def add_message(self, message: ConversationMessage) -> None:
        async with self._lock:
            self._messages.setdefault(message.conversation_id, []).append(message)
            conversation = self._conversations.get(message.conversation_id)
            if conversation is not None:
                self._conversations[message.conversation_id] = replace(
                    conversation, updated_at=message.created_at
                )

    async def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        async with self._lock:
            return tuple(self._messages.get(conversation_id, ()))

    async def get_context(self, conversation_id: str) -> ConversationContext | None:
        async with self._lock:
            return self._contexts.get(conversation_id)

    async def upsert_context(self, context: ConversationContext) -> None:
        async with self._lock:
            self._contexts[context.conversation_id] = context

    async def create_run(self, run: AgentRun) -> None:
        async with self._lock:
            if any(
                item.conversation_id == run.conversation_id
                and item.status.value in {"queued", "running"}
                for item in self._runs.values()
            ):
                raise ActiveRunConflictError(
                    f"Conversation already has an active Run: {run.conversation_id}"
                )
            self._runs[run.id] = replace(run)
            self._events[run.id] = []
            self._event_conditions[run.id] = asyncio.Condition()

    async def create_run_with_message(
        self, run: AgentRun, message: ConversationMessage
    ) -> None:
        async with self._lock:
            if any(
                item.conversation_id == run.conversation_id
                and item.status.value in {"queued", "running"}
                for item in self._runs.values()
            ):
                raise ActiveRunConflictError(
                    f"Conversation already has an active Run: {run.conversation_id}"
                )
            self._runs[run.id] = replace(run)
            self._events[run.id] = []
            self._event_conditions[run.id] = asyncio.Condition()
            self._messages.setdefault(message.conversation_id, []).append(message)
            conversation = self._conversations.get(message.conversation_id)
            if conversation is not None:
                self._conversations[message.conversation_id] = replace(
                    conversation, updated_at=message.created_at
                )

    async def get_run(self, run_id: str) -> AgentRun | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return replace(run) if run else None

    async def list_runs(self, conversation_id: str) -> Sequence[AgentRun]:
        async with self._lock:
            return tuple(
                replace(run)
                for run in sorted(self._runs.values(), key=lambda item: item.created_at)
                if run.conversation_id == conversation_id
            )

    async def update_run(self, run: AgentRun) -> None:
        async with self._lock:
            self._runs[run.id] = replace(run)
        condition = self._event_conditions.get(run.id)
        if condition is not None:
            async with condition:
                condition.notify_all()

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        async with self._lock:
            events = self._events.setdefault(event.run_id, [])
            persisted = AgentEvent(
                event.run_id, (events[-1].sequence if events else 0) + 1,
                event.type, event.data,
            )
            events.append(persisted)
        condition = self._event_conditions.get(event.run_id)
        if condition is not None:
            async with condition:
                condition.notify_all()
        return persisted

    async def list_events(self, run_id: str, after: int = 0) -> Sequence[AgentEvent]:
        async with self._lock:
            return tuple(event for event in self._events.get(run_id, ()) if event.sequence > after)

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float
    ) -> Sequence[AgentEvent]:
        existing = await self.list_events(run_id, after)
        if existing:
            return existing
        condition = self._event_conditions.get(run_id)
        if condition is None:
            return ()
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout_seconds)
        except TimeoutError:
            return ()
        return await self.list_events(run_id, after)

    async def list_recoverable_runs(self) -> Sequence[AgentRun]:
        async with self._lock:
            return tuple(
                replace(run) for run in self._runs.values() if run.status.value == "queued"
            )

    async def get_trigger_message(self, run_id: str) -> ConversationMessage | None:
        async with self._lock:
            return next((
                message
                for messages in self._messages.values()
                for message in messages
                if message.run_id == run_id and message.role == "user"
            ), None)

    async def register_runtime(self, runtime_id: str) -> None:
        async with self._lock:
            self._runtimes[runtime_id] = "active"

    async def heartbeat_runtime(self, runtime_id: str) -> None:
        async with self._lock:
            self._runtimes[runtime_id] = "active"

    async def unregister_runtime(self, runtime_id: str) -> None:
        async with self._lock:
            self._runtimes[runtime_id] = "stopped"

    async def prepare_recovery(self, runtime_id: str, stale_after_seconds: int) -> None:
        async with self._lock:
            now = utc_now()
            stale = [
                attempt for attempt in self._attempts.values()
                if attempt.status == AttemptStatus.RUNNING
                and attempt.lease_owner != runtime_id
                and (
                    attempt.lease_owner is None
                    or self._runtimes.get(attempt.lease_owner) != "active"
                    or attempt.lease_expires_at is None
                    or attempt.lease_expires_at <= now
                )
            ]
            for attempt in stale:
                attempt.status = AttemptStatus.INTERRUPTED
                attempt.completed_at, attempt.error_code = now, "RUNTIME_RESTARTED"
                attempt.lease_owner, attempt.lease_expires_at = None, None
                node = self._nodes[attempt.node_id]
                node.status = TaskNodeStatus.PENDING
                node.started_at = node.completed_at = None
                node.error_code = None
                graph = self._graphs[attempt.graph_id]
                graph.status = TaskGraphStatus.PENDING
                graph.updated_at, graph.completed_at = now, None
                run = self._runs[graph.run_id]
                run.status = RunStatus.QUEUED
                run.answer, run.error_code = "", None
                run.started_at = run.completed_at = None

    async def create_task_graph(
        self, graph: TaskGraph, root_node: TaskNode, root_gate: TaskGate
    ) -> None:
        async with self._lock:
            self._graphs[graph.id] = replace(graph)
            self._graph_run_ids[graph.run_id] = graph.id
            self._nodes[root_node.id] = replace(root_node)
            self._gates[root_gate.id] = replace(root_gate)

    async def get_task_graph(self, run_id: str) -> TaskGraphSnapshot | None:
        async with self._lock:
            graph_id = self._graph_run_ids.get(run_id)
            if graph_id is None:
                return None
            return TaskGraphSnapshot(
                replace(self._graphs[graph_id]),
                tuple(replace(item) for item in self._nodes.values() if item.graph_id == graph_id),
                tuple(replace(item) for item in self._gates.values() if item.graph_id == graph_id),
                tuple(
                    replace(item) for item in self._attempts.values() if item.graph_id == graph_id
                ),
            )

    async def update_task_graph(self, graph: TaskGraph) -> None:
        async with self._lock:
            self._graphs[graph.id] = replace(graph)

    async def compare_and_swap_task_graph(
        self, graph: TaskGraph, expected_version: int
    ) -> bool:
        async with self._lock:
            current = self._graphs.get(graph.id)
            if current is None or current.version != expected_version:
                return False
            self._graphs[graph.id] = replace(graph)
            return True

    async def upsert_task_node(self, node: TaskNode) -> None:
        async with self._lock:
            self._nodes[node.id] = replace(node)

    async def upsert_task_gate(self, gate: TaskGate) -> None:
        async with self._lock:
            self._gates[gate.id] = replace(gate)

    async def create_node_attempt(self, attempt: NodeAttempt) -> None:
        async with self._lock:
            self._attempts[attempt.id] = replace(attempt)

    async def update_node_attempt(self, attempt: NodeAttempt) -> None:
        async with self._lock:
            self._attempts[attempt.id] = replace(attempt)

    async def claim_task_node(
        self, node_id: str, lease_owner: str, lease_seconds: int
    ) -> NodeAttempt | None:
        async with self._lock:
            node = self._nodes.get(node_id)
            if node is None or node.status.value != "pending":
                return None
            if any(
                self._nodes.get(dependency) is None
                or self._nodes[dependency].status.value != "completed"
                or any(
                    gate.required and gate.status.value != "passed"
                    for gate in self._gates.values() if gate.node_id == dependency
                )
                for dependency in node.dependencies
            ):
                return None
            now = utc_now()
            numbers = [
                item.attempt_number for item in self._attempts.values()
                if item.node_id == node_id
            ]
            attempt = NodeAttempt(
                str(uuid4()), node.graph_id, node.id, max(numbers, default=0) + 1,
                lease_owner=lease_owner,
                lease_expires_at=now + timedelta(seconds=lease_seconds),
                started_at=now,
            )
            node.status = TaskNodeStatus.RUNNING
            node.started_at = now
            self._nodes[node.id] = replace(node)
            self._attempts[attempt.id] = replace(attempt)
            return replace(attempt)

    async def commit_task_node(
        self, node: TaskNode, gate: TaskGate, attempt: NodeAttempt
    ) -> None:
        async with self._lock:
            attempt.lease_owner, attempt.lease_expires_at = None, None
            self._nodes[node.id] = replace(node)
            self._gates[gate.id] = replace(gate)
            self._attempts[attempt.id] = replace(attempt)

    async def close_open_task_nodes(
        self, run_id: str, cancelled: bool, error_code: str
    ) -> None:
        async with self._lock:
            graph_id = self._graph_run_ids.get(run_id)
            if graph_id is None:
                return
            now = utc_now()
            for node in self._nodes.values():
                if (
                    node.graph_id == graph_id and node.kind != "orchestration"
                    and node.status.value in {"pending", "running"}
                ):
                    node.status = (
                        TaskNodeStatus.CANCELLED if cancelled else TaskNodeStatus.SKIPPED
                    )
                    node.completed_at, node.error_code = now, error_code
            for gate in self._gates.values():
                if gate.graph_id == graph_id and gate.status.value == "pending":
                    gate.status = GateStatus.BLOCKED
                    gate.verdict, gate.evaluated_at = error_code, now
            for attempt in self._attempts.values():
                if attempt.graph_id == graph_id and attempt.status == AttemptStatus.RUNNING:
                    attempt.status = (
                        AttemptStatus.CANCELLED if cancelled else AttemptStatus.FAILED
                    )
                    attempt.completed_at, attempt.error_code = now, error_code
                    attempt.lease_owner, attempt.lease_expires_at = None, None
