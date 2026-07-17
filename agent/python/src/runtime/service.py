from __future__ import annotations

import asyncio
from typing import Sequence
from uuid import uuid4

from framework import (
    ActiveRunConflictError, AgentEvent, AgentHarness, AgentRepository, AgentRun, Conversation,
    ConversationContext, ConversationMessage, RunResult, RunStatus, TaskGraphSnapshot, utc_now,
)
from .context import ConversationContextManager
from .task_graph import TaskGraphController


class ResourceNotFoundError(LookupError):
    pass


class RunConflictError(RuntimeError):
    pass


class AgentRuntimeService:
    """Own Conversation/Run lifecycle around the infrastructure-free AgentHarness Port.

    HTTP queues a Run here; this service restores durable history, prepares token-bounded context,
    executes Harness steps, persists ordered events, and writes the final assistant message.
    """

    def __init__(
        self, harness: AgentHarness, repository: AgentRepository, max_concurrent_runs: int = 4,
        context_manager: ConversationContextManager | None = None,
    ) -> None:
        if max_concurrent_runs < 1:
            raise ValueError("max_concurrent_runs must be positive.")
        self._harness = harness
        self._repository = repository
        self._context_manager = context_manager or ConversationContextManager()
        self._run_slots = asyncio.Semaphore(max_concurrent_runs)
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._active_conversation_runs: dict[str, str] = {}
        self._task_lock = asyncio.Lock()
        self._instance_id = f"python-runtime:{uuid4()}"
        self._graphs = TaskGraphController(repository, self._instance_id)
        self._started = False
        self._shutting_down = False
        self._heartbeat_task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        """Resume Runs returned to the durable queue after an unclean process stop."""

        async with self._task_lock:
            if self._started:
                return
            self._started = True
        await self._repository.register_runtime(self._instance_id)
        await self._repository.prepare_recovery(self._instance_id, stale_after_seconds=30)
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(), name=f"runtime-heartbeat:{self._instance_id}"
        )
        for run in await self._repository.list_recoverable_runs():
            trigger = await self._repository.get_trigger_message(run.id)
            if trigger is None:
                run.status = RunStatus.FAILED
                run.error_code = "RECOVERY_TRIGGER_MISSING"
                run.answer = "Cannot recover Run because its trigger message is missing."
                run.completed_at = utc_now()
                await self._repository.update_run(run)
                continue
            await self._graphs.ensure(run, trigger.content)
            await self._schedule(run, trigger)

    async def shutdown(self) -> None:
        # Shutdown is an infrastructure interruption, not a user cancellation. Active Runs stay
        # recoverable and their leased Attempts are interrupted by the next Runtime instance.
        self._shutting_down = True
        async with self._task_lock:
            tasks = tuple(self._tasks.values())
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            await asyncio.gather(self._heartbeat_task, return_exceptions=True)
            self._heartbeat_task = None
        await self._repository.unregister_runtime(self._instance_id)

    async def _heartbeat_loop(self) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                await self._repository.heartbeat_runtime(self._instance_id)
        except asyncio.CancelledError:
            raise

    async def create_conversation(self, title: str | None = None) -> Conversation:
        now = utc_now()
        conversation = Conversation(
            id=str(uuid4()), title=title.strip() if title and title.strip() else None,
            created_at=now, updated_at=now,
        )
        await self._repository.create_conversation(conversation)
        return conversation

    async def get_conversation(self, conversation_id: str) -> Conversation:
        conversation = await self._repository.get_conversation(conversation_id)
        if conversation is None:
            raise ResourceNotFoundError(f"Conversation not found: {conversation_id}")
        return conversation

    async def list_conversations(self) -> Sequence[Conversation]:
        return await self._repository.list_conversations()

    async def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        await self.get_conversation(conversation_id)
        return await self._repository.list_messages(conversation_id)

    async def get_context(self, conversation_id: str) -> ConversationContext | None:
        await self.get_conversation(conversation_id)
        return await self._repository.get_context(conversation_id)

    async def submit_message(self, conversation_id: str, content: str) -> AgentRun:
        await self.get_conversation(conversation_id)
        if not content.strip():
            raise ValueError("Message content cannot be empty.")

        run = AgentRun(id=str(uuid4()), conversation_id=conversation_id)
        async with self._task_lock:
            active_run_id = self._active_conversation_runs.get(conversation_id)
            if active_run_id is not None:
                raise RunConflictError(
                    f"Conversation already has an active run: {active_run_id}"
                )
            self._active_conversation_runs[conversation_id] = run.id
        message = ConversationMessage(
            id=str(uuid4()), conversation_id=conversation_id, role="user",
            content=content.strip(), run_id=run.id, created_at=utc_now(),
        )
        try:
            await self._repository.create_run_with_message(run, message)
        except ActiveRunConflictError as exc:
            async with self._task_lock:
                if self._active_conversation_runs.get(conversation_id) == run.id:
                    self._active_conversation_runs.pop(conversation_id, None)
            raise RunConflictError(str(exc)) from exc
        except Exception:
            async with self._task_lock:
                if self._active_conversation_runs.get(conversation_id) == run.id:
                    self._active_conversation_runs.pop(conversation_id, None)
            raise

        parent_graph_id = None
        previous_runs = await self._repository.list_runs(conversation_id)
        for previous in reversed(previous_runs):
            if previous.id == run.id or previous.status != RunStatus.COMPLETED:
                continue
            previous_graph = await self._repository.get_task_graph(previous.id)
            if previous_graph is not None:
                parent_graph_id = previous_graph.graph.id
                break
        await self._graphs.ensure(run, message.content, parent_graph_id=parent_graph_id)
        await self._schedule(run, message)
        return run

    async def _schedule(self, run: AgentRun, trigger: ConversationMessage) -> None:
        async with self._task_lock:
            active_run_id = self._active_conversation_runs.get(run.conversation_id)
            if active_run_id is not None and active_run_id != run.id:
                raise RunConflictError(
                    f"Conversation already has an active run: {active_run_id}"
                )
            self._active_conversation_runs[run.conversation_id] = run.id
            if run.id in self._tasks:
                return
            task = asyncio.create_task(
                self._execute(run, trigger), name=f"agent-run:{run.id}"
            )
            self._tasks[run.id] = task
        task.add_done_callback(
            lambda _: asyncio.create_task(self._forget_task(run.id, run.conversation_id))
        )

    async def get_run(self, run_id: str) -> AgentRun:
        run = await self._repository.get_run(run_id)
        if run is None:
            raise ResourceNotFoundError(f"Run not found: {run_id}")
        return run

    async def list_runs(self, conversation_id: str) -> Sequence[AgentRun]:
        await self.get_conversation(conversation_id)
        return await self._repository.list_runs(conversation_id)

    async def list_events(self, run_id: str, after: int = 0) -> Sequence[AgentEvent]:
        await self.get_run(run_id)
        return await self._repository.list_events(run_id, after)

    async def get_latest_loop_checkpoint(
        self, run_id: str, kind: str | None = None
    ) -> AgentEvent | None:
        events = await self.list_events(run_id)
        for event in reversed(events):
            if event.type != "loop_checkpoint":
                continue
            state = event.data.get("state")
            if kind is None or (isinstance(state, dict) and state.get("kind") == kind):
                return event
        return None

    async def get_task_graph(self, run_id: str) -> TaskGraphSnapshot:
        await self.get_run(run_id)
        snapshot = await self._repository.get_task_graph(run_id)
        if snapshot is None:
            raise ResourceNotFoundError(f"TaskGraph not found for Run: {run_id}")
        return snapshot

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float = 15.0
    ) -> Sequence[AgentEvent]:
        await self.get_run(run_id)
        return await self._repository.wait_for_events(run_id, after, timeout_seconds)

    async def cancel_run(self, run_id: str) -> AgentRun:
        run = await self.get_run(run_id)
        if run.status in {RunStatus.COMPLETED, RunStatus.FAILED, RunStatus.CANCELLED}:
            raise RunConflictError(f"Run is already terminal: {run.status}")
        async with self._task_lock:
            task = self._tasks.get(run_id)
        if task is None:
            raise RunConflictError("Run task is not active.")
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        return await self.get_run(run_id)

    async def _execute(self, run: AgentRun, trigger: ConversationMessage) -> None:
        graph_state = None
        try:
            async with self._run_slots:
                graph_state = await self._graphs.start(run, trigger.content)
                run.status = RunStatus.RUNNING
                run.started_at = utc_now()
                await self._repository.update_run(run)
                stored = await self._repository.list_messages(run.conversation_id)
                # Durable messages are the source of truth; summaries are derived context snapshots.
                context_window = await self._context_manager.build(
                    run.conversation_id,
                    tuple(message for message in stored if message.id != trigger.id),
                    self._repository,
                )
                history = context_window.messages
                run.metadata = {
                    **dict(run.metadata),
                    "context": {
                        "mode": context_window.mode,
                        "total_message_count": context_window.total_message_count,
                        "included_message_count": context_window.included_message_count,
                        "compacted_message_count": context_window.compacted_message_count,
                        "original_tokens": context_window.original_tokens,
                        "compaction_performed": context_window.compaction_performed,
                        "summary_reused": context_window.summary_reused,
                    },
                }
                await self._repository.update_run(run)

                async def save_event(event: AgentEvent):
                    persisted = await self._repository.append_event(event)
                    return await self._graphs.record_event(run, persisted)

                result = await self._harness.run(
                    trigger.content, history=history, on_event=save_event, run_id=run.id
                )
                if self._shutting_down and result.status == RunStatus.CANCELLED:
                    await self._interrupt_for_shutdown(run)
                    return
                run.status = result.status
                run.skill_id = result.skill_id
                run.answer = result.answer
                run.error_code = result.error_code
                run.steps = result.steps
                run.completed_at = utc_now()
                await self._graphs.finish(*graph_state, result)
                # Publish terminal Run state only after graph truth and gates are durable.
                await self._repository.update_run(run)
                if result.status == RunStatus.COMPLETED:
                    await self._repository.add_message(ConversationMessage(
                        id=str(uuid4()), conversation_id=run.conversation_id,
                        role="assistant", content=result.answer, run_id=run.id,
                        created_at=utc_now(),
                    ))
        except asyncio.CancelledError:
            if self._shutting_down:
                await self._interrupt_for_shutdown(run)
                raise
            run.status = RunStatus.CANCELLED
            run.error_code = "RUN_CANCELLED"
            run.answer = "Run was cancelled."
            run.completed_at = utc_now()
            await self._repository.update_run(run)
            existing_events = await self._repository.list_events(run.id)
            next_sequence = existing_events[-1].sequence + 1 if existing_events else 1
            await self._repository.append_event(AgentEvent(
                run_id=run.id,
                sequence=next_sequence,
                type="run_cancelled",
                data={"error_code": run.error_code},
            ))
            if graph_state is not None:
                await self._graphs.finish(
                    *graph_state,
                    RunResult(
                        run.id, RunStatus.CANCELLED, run.answer, None,
                        run.steps, (), run.error_code,
                    ),
                )
            raise
        except Exception as exc:
            run.status = RunStatus.FAILED
            run.error_code = "UNEXPECTED_RUNTIME_ERROR"
            run.answer = str(exc)
            run.completed_at = utc_now()
            await self._repository.update_run(run)
            existing_events = await self._repository.list_events(run.id)
            next_sequence = existing_events[-1].sequence + 1 if existing_events else 1
            await self._repository.append_event(AgentEvent(
                run_id=run.id, sequence=next_sequence, type="run_failed",
                data={"error_code": run.error_code, "message": run.answer},
            ))
            if graph_state is not None:
                await self._graphs.fail_unexpected(*graph_state, run.error_code)

    async def _interrupt_for_shutdown(self, run: AgentRun) -> None:
        """Return an active Run to the durable queue without closing its TaskGraph."""

        run.status = RunStatus.QUEUED
        run.error_code = None
        run.answer = ""
        run.started_at = None
        run.completed_at = None
        await self._repository.update_run(run)
        await self._repository.append_event(AgentEvent(
            run_id=run.id,
            sequence=0,
            type="run_interrupted",
            data={"reason": "runtime_shutdown", "runtime_instance_id": self._instance_id},
        ))

    async def _forget_task(self, run_id: str, conversation_id: str) -> None:
        async with self._task_lock:
            self._tasks.pop(run_id, None)
            if self._active_conversation_runs.get(conversation_id) == run_id:
                self._active_conversation_runs.pop(conversation_id, None)
