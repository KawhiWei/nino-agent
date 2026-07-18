from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from dataclasses import replace
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient

from api.app import create_app
from framework import (
    ActiveRunConflictError, AgentEvent, AgentRun, AttemptStatus, Conversation,
    ConversationMessage, Message, RunResult, RunStatus, utc_now,
)
from infrastructure import SqliteAgentRepository
from runtime import AgentRuntimeService
from runtime.task_graph import TaskGraphController


class GraphRuntime:
    async def run(
        self, user_input: str, history: Sequence[Message] = (), on_event=None,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or "run"
        events = (
            AgentEvent(run_id, 1, "run_started"),
            AgentEvent(run_id, 2, "agent_started", {
                "child_run_id": "child-1", "agent_id": "nino.analyst",
                "skill_id": "nino-data.analysis", "task": "Query an order",
            }),
            AgentEvent(run_id, 3, "tool_completed", {
                "child_run_id": "child-1", "agent_id": "nino.analyst",
                "skill_id": "nino-data.analysis", "tool": "nino_data_get_order_detail",
                "is_error": False,
            }),
            AgentEvent(run_id, 4, "agent_completed", {
                "child_run_id": "child-1", "agent_id": "nino.analyst",
                "skill_id": "nino-data.analysis", "status": "completed",
            }),
            AgentEvent(run_id, 5, "run_completed"),
        )
        if on_event is not None:
            for event in events:
                result = on_event(event)
                if result is not None:
                    await result
        return RunResult(
            run_id, RunStatus.COMPLETED, f"answer:{user_input}",
            "nino-data.analysis", 1, events,
        )


class BlockingRuntime:
    def __init__(self) -> None:
        self.started = asyncio.Event()

    async def run(
        self, user_input: str, history: Sequence[Message] = (), on_event=None,
        run_id: str | None = None,
    ) -> RunResult:
        run_id = run_id or "run"
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            # Real Harness implementations convert cancellation into a cancelled RunResult.
            return RunResult(
                run_id, RunStatus.CANCELLED, "Run was cancelled.", None, 0, (),
                "RUN_CANCELLED",
            )


def wait_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        value = client.get(f"/api/v1/runs/{run_id}").json()
        if value["status"] in {"completed", "failed", "cancelled"}:
            return value
        time.sleep(0.01)
    raise AssertionError("Run did not become terminal.")


class DurableTaskGraphTests(unittest.TestCase):
    def test_api_exposes_nodes_attempts_and_passed_evidence_gates(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_app(
                harness=GraphRuntime(),
                repository=SqliteAgentRepository(Path(root) / "agent.db"),
                runtime_mode="test",
            )) as client:
                conversation = client.post("/api/v1/conversations", json={}).json()
                accepted = client.post(
                    f"/api/v1/conversations/{conversation['id']}/messages",
                    json={"content": "查询订单 DEMO-001"},
                ).json()
                wait_terminal(client, accepted["run_id"])
                graph = client.get(
                    f"/api/v1/runs/{accepted['run_id']}/task-graph"
                ).json()

            self.assertEqual("completed", graph["graph"]["status"])
            self.assertEqual(2, len(graph["nodes"]))
            self.assertEqual(2, len(graph["attempts"]))
            self.assertTrue(all(item["status"] == "passed" for item in graph["gates"]))
            child_gate = next(item for item in graph["gates"] if item["kind"] == "evidence")
            self.assertEqual(["nino_data_get_order_detail"], child_gate["evidence"])

    def test_interrupted_attempt_is_preserved_and_run_resumes(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "agent.db"

            async def seed_interrupted() -> str:
                repository = SqliteAgentRepository(path)
                now = utc_now()
                conversation = Conversation("conversation", None, now, now)
                run = AgentRun("recoverable-run", conversation.id)
                message = ConversationMessage(
                    "trigger", conversation.id, "user", "查询订单 DEMO-001", run.id, now
                )
                await repository.create_conversation(conversation)
                await repository.add_message(message)
                await repository.create_run(run)
                controller = TaskGraphController(repository, "dead-runtime")
                await controller.ensure(run, message.content)
                await controller.start(run, message.content)
                run.status = RunStatus.RUNNING
                run.started_at = now
                await repository.update_run(run)
                return run.id

            run_id = asyncio.run(seed_interrupted())
            recovered_repository = SqliteAgentRepository(path)
            self.assertEqual(
                "running",
                asyncio.run(recovered_repository.get_run(run_id)).status.value,
            )
            with TestClient(create_app(
                harness=GraphRuntime(), repository=recovered_repository, runtime_mode="test"
            )) as client:
                run = wait_terminal(client, run_id)
                graph = client.get(f"/api/v1/runs/{run_id}/task-graph").json()

            root_attempts = [
                item for item in graph["attempts"]
                if item["node_id"].endswith(":orchestration")
            ]
            self.assertEqual("completed", run["status"])
            self.assertEqual([1, 2], [item["attempt_number"] for item in root_attempts])
            self.assertEqual("interrupted", root_attempts[0]["status"])
            self.assertEqual("completed", root_attempts[1]["status"])


class RuntimeShutdownRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_shutdown_interrupts_and_next_runtime_resumes_run(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = SqliteAgentRepository(Path(root) / "agent.db")
            blocking = BlockingRuntime()
            first = AgentRuntimeService(blocking, repository)
            await first.start()
            conversation = await first.create_conversation()
            submitted = await first.submit_message(conversation.id, "查询订单 DEMO-001")
            await asyncio.wait_for(blocking.started.wait(), timeout=1)

            await first.shutdown()
            interrupted = await repository.get_run(submitted.id)
            self.assertEqual(RunStatus.QUEUED, interrupted.status)
            self.assertIn(
                "run_interrupted",
                [event.type for event in await repository.list_events(submitted.id)],
            )

            second = AgentRuntimeService(GraphRuntime(), repository)
            await second.start()
            for _ in range(100):
                recovered = await repository.get_run(submitted.id)
                if recovered.status in {RunStatus.COMPLETED, RunStatus.FAILED}:
                    break
                await asyncio.sleep(0.01)
            await second.shutdown()

            self.assertEqual(RunStatus.COMPLETED, recovered.status)
            graph = await repository.get_task_graph(submitted.id)
            root_attempts = [
                attempt for attempt in graph.attempts
                if attempt.node_id.endswith(":orchestration")
            ]
            self.assertEqual(
                [AttemptStatus.INTERRUPTED, AttemptStatus.COMPLETED],
                [attempt.status for attempt in root_attempts],
            )


class RepositoryConcurrencyTests(unittest.IsolatedAsyncioTestCase):
    async def test_active_run_constraint_cas_and_atomic_event_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = SqliteAgentRepository(Path(root) / "agent.db")
            now = utc_now()
            conversation = Conversation("conversation", None, now, now)
            await repository.create_conversation(conversation)
            first = AgentRun("run-1", conversation.id)
            first_message = ConversationMessage(
                "message-1", conversation.id, "user", "查询订单", first.id, now
            )
            await repository.create_run_with_message(first, first_message)
            second = AgentRun("run-2", conversation.id)
            with self.assertRaises(ActiveRunConflictError):
                await repository.create_run_with_message(
                    second,
                    ConversationMessage(
                        "message-2", conversation.id, "user", "再次查询", second.id, now
                    ),
                )

            controller = TaskGraphController(repository, "runtime")
            snapshot = await controller.ensure(first, first_message.content)
            stale = replace(snapshot.graph, version=snapshot.graph.version + 1)
            self.assertFalse(
                await repository.compare_and_swap_task_graph(stale, expected_version=999)
            )

            persisted = await asyncio.gather(*(
                repository.append_event(AgentEvent(first.id, 0, "parallel", {"index": index}))
                for index in range(20)
            ))
            self.assertEqual(list(range(1, 21)), sorted(item.sequence for item in persisted))
            listed = await repository.list_events(first.id)
            self.assertEqual(list(range(1, 21)), [item.sequence for item in listed])

    async def test_completed_node_claim_is_reused_without_new_attempt(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = SqliteAgentRepository(Path(root) / "agent.db")
            now = utc_now()
            conversation = Conversation("conversation", None, now, now)
            run = AgentRun("run", conversation.id)
            message = ConversationMessage("message", conversation.id, "user", "查询订单", run.id, now)
            await repository.create_conversation(conversation)
            await repository.create_run_with_message(run, message)
            controller = TaskGraphController(repository, "runtime")
            await controller.ensure(run, message.content)
            await controller.start(run, message.content)
            await controller.record_event(run, AgentEvent(run.id, 1, "graph_planned", {
                "revision": 1,
                "nodes": [{
                    "node_id": "query", "kind": "specialist",
                    "agent_id": "nino.analyst", "skill_id": "nino-data.analysis",
                    "task": "Query", "depends_on": [], "gate_kind": "evidence",
                }],
            }))
            started = AgentEvent(run.id, 2, "agent_started", {
                "child_run_id": "child-1", "plan_node_id": "query",
                "agent_id": "nino.analyst", "skill_id": "nino-data.analysis",
                "task": "Query", "depends_on": [], "node_kind": "specialist",
            })
            self.assertTrue((await controller.record_event(run, started))["execute"])
            await controller.record_event(run, AgentEvent(run.id, 3, "tool_completed", {
                "child_run_id": "child-1", "tool": "nino_data_get_order_detail",
                "call_id": "tool-1", "is_error": False,
            }))
            await controller.record_event(run, AgentEvent(run.id, 4, "agent_completed", {
                "child_run_id": "child-1", "plan_node_id": "query", "status": "completed",
                "result_summary": "stored answer",
                "node_result": {
                    "status": "completed", "summary": "stored answer", "evidence": [],
                    "findings": [], "concerns": [], "error_code": None, "retryable": False,
                },
            }))

            reused = await controller.record_event(run, AgentEvent(run.id, 5, "agent_started", {
                **dict(started.data), "child_run_id": "child-2",
            }))
            self.assertFalse(reused["execute"])
            self.assertEqual("already_completed", reused["reason"])
            self.assertEqual("stored answer", reused["result"]["summary"])


if __name__ == "__main__":
    unittest.main()
