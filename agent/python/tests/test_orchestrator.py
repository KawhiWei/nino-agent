from __future__ import annotations

import asyncio
import json
import unittest
from pathlib import Path
from typing import Sequence

from framework import (
    AgentEvent, AgentRun, Conversation, ConversationMessage, Message, ModelTurn,
    RunResult, RunStatus, ToolCall, ToolDefinition, utc_now,
)
from harness import AgentRegistry, OrchestratorHarness, SkillRegistry
from infrastructure import InMemoryAgentRepository
from runtime.task_graph import TaskGraphController


SHARED = Path(__file__).resolve().parents[2] / "shared"


class QueueModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = list(turns)
        self.messages: list[Sequence[Message]] = []
        self.tools: list[Sequence[ToolDefinition]] = []

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        self.messages.append(messages)
        self.tools.append(tools)
        return self.turns.pop(0)


class FakeWorker:
    def __init__(self, status: RunStatus = RunStatus.COMPLETED) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.status = status

    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        answer = (
            json.dumps({
                "verdict": "passed", "evidence_level": "proved",
                "checked_requirements": ["evidence"],
                "failed_requirements": [], "concerns": [],
            })
            if task.startswith("Independently") else "specialist evidence"
        )
        return RunResult(
            run_id, self.status, answer, selected_skill_id,
            1, (), "CHILD_FAILED" if self.status == RunStatus.FAILED else None,
        )


class ConcurrentWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.active = 0
        self.max_active = 0

    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.02)
        self.active -= 1
        answer = (
            json.dumps({
                "verdict": "passed", "evidence_level": "proved",
                "checked_requirements": ["evidence"],
                "failed_requirements": [], "concerns": [],
            })
            if task.startswith("Independently") else "evidence"
        )
        return RunResult(run_id, RunStatus.COMPLETED, answer, selected_skill_id, 1, ())


class SequencedWorker(FakeWorker):
    def __init__(self, outcomes: list[RunStatus]) -> None:
        super().__init__()
        self.outcomes = outcomes

    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        status = self.outcomes.pop(0)
        answer = (
            json.dumps({
                "verdict": "passed", "evidence_level": "proved",
                "checked_requirements": ["evidence"],
                "failed_requirements": [], "concerns": [],
            })
            if task.startswith("Independently") else "repaired evidence"
        )
        return RunResult(
            run_id, status, answer, selected_skill_id, 1, (),
            None if status == RunStatus.COMPLETED else "CHILD_FAILED",
        )


class EvidenceWorker(FakeWorker):
    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        result = await super().run(
            task, run_id=run_id, selected_skill_id=selected_skill_id, on_event=on_event
        )
        if on_event is not None:
            await on_event(AgentEvent(run_id, 1, "tool_completed", {
                "tool": "nino_data_get_order_detail", "call_id": f"tool:{run_id}",
                "is_error": False,
            }))
        return result


class StructuredWorker(FakeWorker):
    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        if task.startswith("Independently"):
            return await super().run(
                task, run_id=run_id, selected_skill_id=selected_skill_id,
                on_event=on_event,
            )
        self.calls.append((task, selected_skill_id))
        return RunResult(
            run_id, RunStatus.COMPLETED,
            json.dumps({
                "status": "completed",
                "summary": "structured upstream",
                "outputs": {"margin": 60, "currency": "CNY"},
                "findings": ["margin is positive"],
                "concerns": [],
                "recommended_next": ["compare with prior month"],
            }),
            selected_skill_id, 1, (),
        )


class OrchestratorHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.skills = SkillRegistry.load(SHARED / "skills")
        self.agents = AgentRegistry.load(SHARED / "agents")

    async def test_semantic_fallback_rejects_general_question_structurally(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall(
            "reject-1", "nino_runtime_reject_request",
            {"reason": "No supplied capability fits the request."},
        ),)))
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("Explain what an API is")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertIn("不在已注册 Skill", result.answer)
        self.assertEqual([], worker.calls)
        self.assertEqual(1, len(model.messages))
        self.assertNotIn("agent_started", [event.type for event in result.events])
        self.assertIn("policy_rejected", [event.type for event in result.events])

    async def test_orchestrator_can_request_top_level_clarification(self) -> None:
        result = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(ToolCall(
                "clarify-1", "nino_runtime_request_clarification",
                {"message": "请提供需要统计的日期范围？"},
            ),))),
            self.skills, self.agents, lambda _: FakeWorker(),
        ).run("统计毛利")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("请提供需要统计的日期范围？", result.answer)
        self.assertIn("clarification_requested", [event.type for event in result.events])

    async def test_rejects_direct_answer_for_matched_skill(self) -> None:
        result = await OrchestratorHarness(
            QueueModel(ModelTurn(text="Unsupported direct answer")),
            self.skills,
            self.agents,
            lambda _: FakeWorker(),
        ).run("Query order DEMO-202607-001")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("DISPATCH_REQUIRED", result.error_code)
        self.assertIn("policy_rejected", [event.type for event in result.events])

    async def test_excluded_write_intent_is_rejected_before_model_call(self) -> None:
        model = QueueModel()
        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: FakeWorker()
        ).run("请创建订单并写入数据库")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertIn("不在已注册 Skill", result.answer)
        self.assertEqual([], model.messages)
        rejected = [event for event in result.events if event.type == "policy_rejected"]
        self.assertEqual("OUT_OF_SCOPE", rejected[0].data["error_code"])

    async def test_rejects_final_answer_when_all_dispatches_failed(self) -> None:
        worker = FakeWorker(RunStatus.FAILED)
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "dispatch-1", "nino_runtime_dispatch_agent", {
                    "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis",
                    "task": "Query one order",
                }
            ),)),
            ModelTurn(text="I will answer despite the failed child."),
        )

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("Query order DEMO-202607-001")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("SUCCESSFUL_DISPATCH_REQUIRED", result.error_code)

    async def test_dispatches_dynamic_agent_and_skill_pair(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "dispatch-1", "nino_runtime_dispatch_agent", {
                    "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis",
                    "task": "Query one order",
                }
            ),)),
            ModelTurn(text="Reconciled answer"),
        )
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("Query order DEMO-202607-001")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("nino-data.analysis", result.skill_id)
        self.assertTrue(worker.calls[0][0].startswith("Query one order\n\n"))
        self.assertEqual("nino-data.analysis", worker.calls[0][1])
        self.assertIn("Acceptance contract:", worker.calls[0][0])
        self.assertEqual(2, len(worker.calls))
        self.assertIn("Claim to evaluate", worker.calls[1][0])
        self.assertIn("agent_started", [event.type for event in result.events])
        self.assertEqual("tool", model.messages[1][-1].role)

    async def test_rejects_agent_skill_pair_outside_catalog(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall(
            "dispatch-1", "nino_runtime_dispatch_agent", {
                "agent_id": "nino-data.analyst",
                "skill_id": "unknown.skill",
                "task": "Do something",
            }
        ),)))

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: FakeWorker()
        ).run("Query order DEMO-202607-001")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("DISPATCH_NOT_ALLOWED", result.error_code)

    async def test_executes_independent_planned_nodes_in_parallel(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(
                ToolCall("call-a", "nino_runtime_dispatch_agent", {
                    "node_id": "summary-a", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query order A",
                }),
                ToolCall("call-b", "nino_runtime_dispatch_agent", {
                    "node_id": "summary-b", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query order B",
                }),
            )),
            ModelTurn(text="Combined answer"),
        )
        worker = ConcurrentWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("统计订单 A 和订单 B")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertGreaterEqual(worker.max_active, 2)
        planned = next(event for event in result.events if event.type == "graph_planned")
        self.assertEqual(
            {"summary-a", "summary-a.verify", "summary-b", "summary-b.verify"},
            {item["node_id"] for item in planned.data["nodes"]},
        )

    async def test_dependency_waits_and_cycle_is_rejected_before_execution(self) -> None:
        worker = ConcurrentWorker()
        model = QueueModel(
            ModelTurn(tool_calls=(
                ToolCall("call-a", "nino_runtime_dispatch_agent", {
                    "node_id": "upstream", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query upstream",
                }),
                ToolCall("call-b", "nino_runtime_dispatch_agent", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "agent_id": "nino-data.analyst", "skill_id": "nino-data.analysis",
                    "task": "Analyze downstream",
                }),
            )),
            ModelTurn(text="Dependent answer"),
        )
        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("统计订单并继续分析")
        analyst_tasks = [
            task.split("\n\n", 1)[0]
            for task, _ in worker.calls if not task.startswith("Independently")
        ]

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(["Query upstream", "Analyze downstream"], analyst_tasks)

        cycle_model = QueueModel(ModelTurn(tool_calls=(
            ToolCall("cycle-a", "nino_runtime_dispatch_agent", {
                "node_id": "a", "depends_on": ["b"], "agent_id": "nino-data.analyst",
                "skill_id": "nino-data.analysis", "task": "A",
            }),
            ToolCall("cycle-b", "nino_runtime_dispatch_agent", {
                "node_id": "b", "depends_on": ["a"], "agent_id": "nino-data.analyst",
                "skill_id": "nino-data.analysis", "task": "B",
            }),
        )))
        cycle_worker = ConcurrentWorker()
        cycle = await OrchestratorHarness(
            cycle_model, self.skills, self.agents, lambda _: cycle_worker
        ).run("统计订单 A 和 B")

        self.assertEqual(RunStatus.FAILED, cycle.status)
        self.assertEqual("INVALID_TASK_GRAPH", cycle.error_code)
        self.assertEqual([], cycle_worker.calls)

    async def test_dependency_result_is_bound_into_downstream_context(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(
                ToolCall("up", "nino_runtime_dispatch_agent", {
                    "node_id": "upstream", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query upstream",
                }),
                ToolCall("down", "nino_runtime_dispatch_agent", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "input_bindings": [{
                        "name": "source_summary", "source_node_id": "upstream",
                        "selector": "summary",
                    }],
                    "agent_id": "nino-data.analyst", "skill_id": "nino-data.analysis",
                    "task": "Analyze upstream result",
                }),
            )),
            ModelTurn(text="Bound answer"),
        )
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("统计订单并继续分析")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        downstream = next(task for task, _ in worker.calls if task.startswith("Analyze upstream"))
        self.assertIn("Bound upstream inputs:", downstream)
        self.assertIn('"source_summary": "specialist evidence"', downstream)

    async def test_structured_outputs_can_be_selected_by_input_binding(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(
                ToolCall("up", "nino_runtime_dispatch_agent", {
                    "node_id": "upstream", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query margin",
                }),
                ToolCall("down", "nino_runtime_dispatch_agent", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "input_bindings": [{
                        "name": "metrics", "source_node_id": "upstream",
                        "selector": "outputs",
                    }],
                    "agent_id": "nino-data.analyst", "skill_id": "nino-data.analysis",
                    "task": "Explain metrics",
                }),
            )),
            ModelTurn(text="Structured answer"),
        )
        worker = StructuredWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("统计订单毛利并解释")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        downstream = next(task for task, _ in worker.calls if task.startswith("Explain metrics"))
        self.assertIn('"metrics": {"margin": 60, "currency": "CNY"}', downstream)

    async def test_input_binding_must_reference_a_dependency(self) -> None:
        result = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(ToolCall(
                "bad", "nino_runtime_dispatch_agent", {
                    "node_id": "downstream",
                    "input_bindings": [{
                        "name": "source", "source_node_id": "not-a-dependency",
                        "selector": "summary",
                    }],
                    "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Analyze",
                },
            ),))),
            self.skills, self.agents, lambda _: FakeWorker(),
        ).run("统计订单")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("INVALID_INPUT_BINDING", result.error_code)

    async def test_failed_revision_can_reconcile_with_new_repair_node(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "first", "nino_runtime_dispatch_agent", {
                    "node_id": "initial", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Initial query",
                },
            ),)),
            ModelTurn(tool_calls=(ToolCall(
                "repair", "nino_runtime_dispatch_agent", {
                    "node_id": "repair", "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis", "task": "Repair with corrected query",
                },
            ),)),
            ModelTurn(text="Reconciled answer"),
        )
        worker = SequencedWorker([
            RunStatus.FAILED, RunStatus.COMPLETED, RunStatus.COMPLETED,
        ])

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("统计订单并在查询失败时修正")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        graph_events = [
            event.type for event in result.events
            if event.type in {"graph_planned", "graph_reconciled"}
        ]
        self.assertEqual(["graph_planned", "graph_reconciled"], graph_events)

    async def test_reuses_completed_worker_and_evaluator_nodes_after_root_restart(self) -> None:
        repository = InMemoryAgentRepository()
        now = utc_now()
        conversation = Conversation("conversation", None, now, now)
        run = AgentRun("recoverable", conversation.id)
        trigger = ConversationMessage(
            "trigger", conversation.id, "user", "Query order DEMO-202607-001", run.id, now
        )
        await repository.create_conversation(conversation)
        await repository.create_run_with_message(run, trigger)
        controller = TaskGraphController(repository, "runtime")
        await controller.ensure(run, trigger.content)
        await controller.start(run, trigger.content)

        dispatch = ToolCall("call", "nino_runtime_dispatch_agent", {
            "node_id": "query", "agent_id": "nino-data.analyst",
            "skill_id": "nino-data.analysis", "task": "Query one order",
            "acceptance_contract": {
                "spec_source": "user_request",
                "target_outcome": "Return the exact order margin in CNY.",
                "positive_checks": ["Order id and margin match Tool evidence."],
                "negative_checks": ["Do not include another order."],
                "evidence_requirements": ["Order detail Tool result."],
                "gaps": [],
                "pass_label": "order_margin_verified",
            },
        })

        async def project(event: AgentEvent):
            return await controller.record_event(run, event)

        first_worker = EvidenceWorker()
        first = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(dispatch,)), ModelTurn(text="first answer")),
            self.skills, self.agents, lambda _: first_worker,
        ).run(trigger.content, on_event=project, run_id=run.id)
        self.assertEqual(RunStatus.COMPLETED, first.status)
        self.assertEqual(2, len(first_worker.calls))
        first_snapshot = await repository.get_task_graph(run.id)
        query_node = next(
            item for item in first_snapshot.nodes
            if item.metadata.get("logical_node_id") == "query"
        )
        self.assertEqual("Return the exact order margin in CNY.", query_node.contract.target_outcome)
        self.assertEqual("order_margin_verified", query_node.contract.pass_label)
        self.assertIn("Acceptance contract:", first_worker.calls[1][0])

        unexpected_worker = FakeWorker(RunStatus.FAILED)
        resumed = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(dispatch,)), ModelTurn(text="reused answer")),
            self.skills, self.agents, lambda _: unexpected_worker,
        ).run(trigger.content, on_event=project, run_id=run.id)

        self.assertEqual(RunStatus.COMPLETED, resumed.status)
        self.assertEqual([], unexpected_worker.calls)


if __name__ == "__main__":
    unittest.main()
