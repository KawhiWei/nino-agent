from __future__ import annotations

import importlib.util
import json
import unittest
from typing import Sequence

from framework import (
    Message, ModelTurn, RunStatus, ToolCall, ToolDefinition, ToolResult,
)
from harness import LangGraphReActHarness, Skill, SkillRegistry


class QueueModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = list(turns)

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        return self.turns.pop(0)


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> Sequence[ToolDefinition]:
        return (ToolDefinition("order", "Get order", {"type": "object"}),)

    async def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(json.dumps({"found": True}))


def registry() -> SkillRegistry:
    return SkillRegistry((Skill(
        id="nino-data.test", name="Nino Data", version="1.0.0", description="Test",
        instructions="Use tools.", intent_keywords=("订单",),
        allowed_tools=frozenset({"order"}), max_steps=4, is_default=True,
    ),))


@unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph optional dependency")
class LangGraphHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_executes_model_tool_model_graph(self) -> None:
        tools = FakeTools()
        harness = LangGraphReActHarness(
            QueueModel(
                ModelTurn(tool_calls=(ToolCall("call-1", "order", {"id": "1"}),)),
                ModelTurn(text="订单毛利为 60 元。"),
            ),
            tools,
            registry(),
        )

        result = await harness.run("查询订单")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual(2, result.steps)
        self.assertEqual(1, len(tools.calls))
        self.assertIn("tool_completed", [event.type for event in result.events])


if __name__ == "__main__":
    unittest.main()
