from __future__ import annotations

import unittest
from pathlib import Path
from typing import Sequence

from framework import Message, ModelTurn, RunResult, RunStatus, ToolCall, ToolDefinition
from harness import AgentRegistry, OrchestratorHarness, SkillRegistry


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
        return RunResult(
            run_id, self.status, "specialist evidence", selected_skill_id,
            1, (), "CHILD_FAILED" if self.status == RunStatus.FAILED else None,
        )


class OrchestratorHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.skills = SkillRegistry.load(SHARED / "skills")
        self.agents = AgentRegistry.load(SHARED / "agents")

    async def test_rejects_general_question_before_model_call(self) -> None:
        model = QueueModel()
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("Explain what an API is")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertIn("不在已注册 Skill", result.answer)
        self.assertEqual([], worker.calls)
        self.assertEqual([], model.messages)
        self.assertNotIn("agent_started", [event.type for event in result.events])
        self.assertIn("policy_rejected", [event.type for event in result.events])

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
        self.assertEqual([("Query one order", "nino-data.analysis")], worker.calls)
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


if __name__ == "__main__":
    unittest.main()
