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
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def run(
        self, task: str, *, run_id: str, selected_skill_id: str | None = None,
        on_event=None,
    ) -> RunResult:
        self.calls.append((task, selected_skill_id))
        return RunResult(
            run_id, RunStatus.COMPLETED, "specialist evidence", selected_skill_id,
            1, (),
        )


class OrchestratorHarnessTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.skills = SkillRegistry.load(SHARED / "skills")
        self.agents = AgentRegistry.load(SHARED / "agents")

    async def test_answers_general_question_without_dispatch(self) -> None:
        model = QueueModel(ModelTurn(text="A general answer"))
        worker = FakeWorker()

        result = await OrchestratorHarness(
            model, self.skills, self.agents, lambda _: worker
        ).run("Explain what an API is")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("A general answer", result.answer)
        self.assertEqual([], worker.calls)
        self.assertNotIn("agent_started", [event.type for event in result.events])
        self.assertIn("nino-data.analysis", model.messages[0][1].content)

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
        ).run("Do something")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("DISPATCH_NOT_ALLOWED", result.error_code)


if __name__ == "__main__":
    unittest.main()
