from __future__ import annotations

import asyncio
import json
import unittest
from dataclasses import replace
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


class StreamingQueueModel(QueueModel):
    async def stream_complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition],
        on_text_delta=None,
    ) -> ModelTurn:
        self.messages.append(messages)
        self.tools.append(tools)
        turn = self.turns.pop(0)
        for delta in ("流式", "答案"):
            if on_text_delta is not None:
                value = on_text_delta(delta)
                if asyncio.iscoroutine(value):
                    await value
        return turn


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


class AssuranceRepairWorker(FakeWorker):
    def __init__(self) -> None:
        super().__init__()
        self.verification_count = 0

    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        if on_event is not None:
            await on_event(AgentEvent(run_id, 1, "tool_completed", {
                "tool": "nino_data_query_summary", "call_id": f"tool:{run_id}",
                "is_error": False,
            }))
        if task.startswith("Independently"):
            self.verification_count += 1
            passed = self.verification_count > 1
            answer = json.dumps({
                "verdict": "passed" if passed else "failed",
                "evidence_level": "proved",
                "checked_requirements": ["evidence"],
                "failed_requirements": [] if passed else ["consistent summary"],
                "concerns": [] if passed else ["summary contradicts outputs"],
            })
        else:
            answer = json.dumps({
                "status": "completed", "summary": "verified repair candidate",
                "outputs": {"lowest_rate": "AIR_TICKET"},
            })
        return RunResult(
            run_id, RunStatus.COMPLETED, answer, selected_skill_id, 1, (),
        )


class ClarificationWorker(FakeWorker):
    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        message = "请提供需要查询的订单号。"
        if on_event is not None:
            await on_event(AgentEvent(
                run_id, 1, "clarification_requested", {"message": message}
            ))
        return RunResult(
            run_id, RunStatus.COMPLETED, message, selected_skill_id, 1, (),
        )


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
        self.assertEqual("当前请求不在支持的能力范围内", result.answer)
        self.assertEqual([], worker.calls)
        self.assertEqual(1, len(model.messages))
        self.assertNotIn("agent_started", [event.type for event in result.events])
        self.assertIn("policy_rejected", [event.type for event in result.events])

    async def test_semantic_fallback_rejects_react_question(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall(
            "reject-react", "nino_runtime_reject_request",
            {"reason": "No supplied capability fits the request."},
        ),)))
        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: FakeWorker()
        ).run("请解释 ReAct Agent 中 Reason、Action、Observation 的关系。")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertIsNone(result.skill_id)
        self.assertEqual(1, len(model.messages))
        self.assertEqual("当前请求不在支持的能力范围内", result.answer)

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

    async def test_worker_clarification_terminates_without_verifier(self) -> None:
        worker = ClarificationWorker()
        result = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(ToolCall(
                "clarify-node", "nino_runtime_submit_task_graph_node", {
                    "node_id": "clarify-order", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis",
                    "task": "Ask for the missing exact order id.",
                },
            ),))),
            self.skills, self.agents, lambda _: worker,
        ).run("查询订单")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("nino-data.analysis", result.skill_id)
        self.assertEqual("请提供需要查询的订单号。", result.answer)
        self.assertEqual(1, len(worker.calls))
        skipped = [event for event in result.events if event.type == "node_skipped"]
        self.assertEqual("clarification_terminal", skipped[0].data["reason"])

    async def test_streams_only_final_reconciliation_as_answer_deltas(self) -> None:
        model = StreamingQueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "query", "nino_runtime_submit_task_graph_node", {
                    "node_id": "query", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query one order",
                },
            ),)),
            ModelTurn(text="流式答案"),
        )
        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: EvidenceWorker(),
        ).run("查询订单 DEMO-202607-001")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        deltas = [
            event.data["delta"] for event in result.events
            if event.type == "answer_delta"
        ]
        self.assertEqual(["流式答案"], deltas)
        self.assertEqual(result.answer, "".join(deltas))

    async def test_rejects_direct_answer_for_matched_skill(self) -> None:
        result = await OrchestratorHarness(
            QueueModel(ModelTurn(text="Unsupported direct answer")),
            self.skills,
            self.agents,
            lambda _: FakeWorker(),
        ).run("Query order DEMO-202607-001")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("INVALID_PLANNER_OUTPUT", result.error_code)
        self.assertIn("policy_rejected", [event.type for event in result.events])

    async def test_unsupported_write_intent_is_rejected_by_capability(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall(
            "reject-write", "nino_runtime_reject_request",
            {"reason": "The registered capability is read-only analysis."},
        ),)))
        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: FakeWorker()
        ).run("请创建订单并写入数据库")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("当前请求不在支持的能力范围内", result.answer)
        self.assertEqual(1, len(model.messages))
        rejected = [event for event in result.events if event.type == "policy_rejected"]
        self.assertEqual("OUT_OF_SCOPE", rejected[0].data["error_code"])

    async def test_rejects_final_answer_when_all_dispatches_failed(self) -> None:
        worker = FakeWorker(RunStatus.FAILED)
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "dispatch-1", "nino_runtime_submit_task_graph_node", {
                    "agent_id": "nino.analyst",
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
        self.assertEqual("INVALID_PLANNER_OUTPUT", result.error_code)

    async def test_dispatches_dynamic_agent_and_skill_pair(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "dispatch-1", "nino_runtime_submit_task_graph_node", {
                    "agent_id": "nino.analyst",
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
        self.assertEqual(
            {"nino_runtime_submit_task_graph_node", "nino_runtime_request_clarification"},
            {tool.name for tool in model.tools[0]},
        )
        self.assertNotIn(
            "nino_data_get_order_detail", {tool.name for tool in model.tools[0]}
        )
        self.assertEqual(2, len(worker.calls))
        self.assertIn("Claim to evaluate", worker.calls[1][0])
        self.assertIn("agent_started", [event.type for event in result.events])
        self.assertEqual("system", model.messages[1][-1].role)
        self.assertEqual((), tuple(model.tools[1]))
        self.assertIn("successful verified node results", model.messages[1][-1].content)
        phases = [
            event.data.get("phase") for event in result.events
            if event.type == "model_started"
        ]
        self.assertEqual(["planning", "reconciliation"], phases)

    async def test_rejects_agent_skill_pair_outside_catalog(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall(
            "dispatch-1", "nino_runtime_submit_task_graph_node", {
                "agent_id": "nino.analyst",
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
                ToolCall("call-a", "nino_runtime_submit_task_graph_node", {
                    "node_id": "summary-a", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query order A",
                }),
                ToolCall("call-b", "nino_runtime_submit_task_graph_node", {
                    "node_id": "summary-b", "agent_id": "nino.analyst",
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
                ToolCall("call-a", "nino_runtime_submit_task_graph_node", {
                    "node_id": "upstream", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query upstream",
                }),
                ToolCall("call-b", "nino_runtime_submit_task_graph_node", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "agent_id": "nino.analyst", "skill_id": "nino-data.analysis",
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
            ToolCall("cycle-a", "nino_runtime_submit_task_graph_node", {
                "node_id": "a", "depends_on": ["b"], "agent_id": "nino.analyst",
                "skill_id": "nino-data.analysis", "task": "A",
            }),
            ToolCall("cycle-b", "nino_runtime_submit_task_graph_node", {
                "node_id": "b", "depends_on": ["a"], "agent_id": "nino.analyst",
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
                ToolCall("up", "nino_runtime_submit_task_graph_node", {
                    "node_id": "upstream", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query upstream",
                }),
                ToolCall("down", "nino_runtime_submit_task_graph_node", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "input_bindings": [{
                        "name": "source_summary", "source_node_id": "upstream",
                        "selector": "summary",
                    }],
                    "agent_id": "nino.analyst", "skill_id": "nino-data.analysis",
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
                ToolCall("up", "nino_runtime_submit_task_graph_node", {
                    "node_id": "upstream", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Query margin",
                }),
                ToolCall("down", "nino_runtime_submit_task_graph_node", {
                    "node_id": "downstream", "depends_on": ["upstream"],
                    "input_bindings": [{
                        "name": "metrics", "source_node_id": "upstream",
                        "selector": "outputs",
                    }],
                    "agent_id": "nino.analyst", "skill_id": "nino-data.analysis",
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
                "bad", "nino_runtime_submit_task_graph_node", {
                    "node_id": "downstream",
                    "input_bindings": [{
                        "name": "source", "source_node_id": "not-a-dependency",
                        "selector": "summary",
                    }],
                    "agent_id": "nino.analyst",
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
                "first", "nino_runtime_submit_task_graph_node", {
                    "node_id": "initial", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Initial query",
                },
            ),)),
            ModelTurn(tool_calls=(ToolCall(
                "repair", "nino_runtime_submit_task_graph_node", {
                    "node_id": "repair", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Repair with corrected query",
                    "supersedes_node_id": "initial",
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

    async def test_assurance_failure_repairs_without_superseding_completed_worker(self) -> None:
        repository = InMemoryAgentRepository()
        now = utc_now()
        conversation = Conversation("assurance-repair", None, now, now)
        run = AgentRun("assurance-repair-run", conversation.id)
        trigger = ConversationMessage(
            "assurance-repair-trigger", conversation.id, "user",
            "重新查询并修正不一致的业务线毛利结论", run.id, now,
        )
        await repository.create_conversation(conversation)
        await repository.create_run_with_message(run, trigger)
        controller = TaskGraphController(repository, "runtime")
        await controller.ensure(run, trigger.content)
        await controller.start(run, trigger.content)

        async def project(event: AgentEvent):
            return await controller.record_event(run, event)

        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "first", "nino_runtime_submit_task_graph_node", {
                    "node_id": "initial", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Initial query",
                },
            ),)),
            ModelTurn(tool_calls=(ToolCall(
                "repair", "nino_runtime_submit_task_graph_node", {
                    "node_id": "repair", "agent_id": "nino.analyst",
                    "skill_id": "nino-data.analysis", "task": "Correct summary",
                    "supersedes_node_id": "initial",
                },
            ),)),
            ModelTurn(text="Corrected verified answer"),
        )
        worker = AssuranceRepairWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run(trigger.content, on_event=project, run_id=run.id)

        self.assertEqual(RunStatus.COMPLETED, result.status)
        reconciled = next(
            event for event in result.events if event.type == "graph_reconciled"
        )
        repair = next(
            node for node in reconciled.data["nodes"] if node["node_id"] == "repair"
        )
        self.assertIsNone(repair["supersedes_node_id"])
        self.assertEqual(2, worker.verification_count)
        self.assertIn(
            "automatically submit an independent repair node",
            model.messages[1][2].content,
        )
        snapshot = await repository.get_task_graph(run.id)
        initial = next(
            node for node in snapshot.nodes
            if node.metadata.get("logical_node_id") == "initial"
        )
        repaired = next(
            node for node in snapshot.nodes
            if node.metadata.get("logical_node_id") == "repair"
        )
        self.assertEqual("completed", initial.status.value)
        self.assertEqual("completed", repaired.status.value)
        self.assertIsNone(repaired.metadata.get("supersedes_node_id"))

    async def test_history_only_follow_up_bypasses_evidence_worker(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "history", "nino_runtime_answer_from_history", {},
            ),)),
            ModelTurn(text="金额看绝对毛利，毛利率看相对收入比例。"),
        )
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run(
            "为什么两个排名不同？",
            history=(Message(
                role="assistant",
                content="TRAIN_TICKET 毛利最低，AIR_TICKET 毛利率最低。",
            ),),
        )

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("金额看绝对毛利，毛利率看相对收入比例。", result.answer)
        self.assertEqual([], worker.calls)
        self.assertIn(
            "nino_runtime_answer_from_history",
            {tool.name for tool in model.tools[0]},
        )
        self.assertIn(
            "TRAIN_TICKET 毛利最低",
            model.messages[1][1].content,
        )

    async def test_history_follow_up_marks_latest_answer_explicitly(self) -> None:
        model = QueueModel(
            ModelTurn(tool_calls=(ToolCall(
                "history", "nino_runtime_answer_from_history", {},
            ),)),
            ModelTurn(text="上一轮订单亏损 450 CNY。"),
        )

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: FakeWorker()
        ).run(
            "上一轮这笔订单盈利还是亏损？",
            history=(
                Message(role="assistant", content="较早订单盈利 100 CNY。"),
                Message(role="user", content="查询另一个订单。"),
                Message(role="assistant", content="最新订单亏损 450 CNY。"),
            ),
        )

        self.assertEqual(RunStatus.COMPLETED, result.status)
        reconciliation_prompt = model.messages[1][1].content
        self.assertIn('"latest_answer": "最新订单亏损 450 CNY。"', reconciliation_prompt)
        self.assertIn('"earlier_answers": ["较早订单盈利 100 CNY。"]', reconciliation_prompt)

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

        dispatch = ToolCall("call", "nino_runtime_submit_task_graph_node", {
            "node_id": "query", "agent_id": "nino.analyst",
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

        versioned_skills = SkillRegistry(tuple(
            replace(skill, version="2.0.0")
            if skill.id == "nino-data.analysis" else skill
            for skill in self.skills.skills
        ))
        changed_worker = EvidenceWorker()
        changed = await OrchestratorHarness(
            QueueModel(ModelTurn(tool_calls=(dispatch,)), ModelTurn(text="versioned answer")),
            versioned_skills, self.agents, lambda _: changed_worker,
        ).run(trigger.content, on_event=project, run_id=run.id)

        self.assertEqual(RunStatus.COMPLETED, changed.status)
        self.assertEqual(2, len(changed_worker.calls))
        changed_snapshot = await repository.get_task_graph(run.id)
        query_nodes = [
            item for item in changed_snapshot.nodes
            if item.metadata.get("logical_node_id") == "query"
        ]
        self.assertEqual(2, len(query_nodes))
        self.assertEqual(
            {"1.1.0", "2.0.0"},
            {item.metadata.get("skill_version") for item in query_nodes},
        )
        old_query = next(
            item for item in query_nodes
            if item.metadata.get("skill_version") == "1.1.0"
        )
        new_query = next(
            item for item in query_nodes
            if item.metadata.get("skill_version") == "2.0.0"
        )
        self.assertEqual("completed", old_query.status.value)
        self.assertIn(
            "superseded_by_node_id", old_query.metadata,
            [dict(item.metadata) for item in query_nodes],
        )
        self.assertEqual(new_query.id, old_query.metadata["superseded_by_node_id"])


if __name__ == "__main__":
    unittest.main()
