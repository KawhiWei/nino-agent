from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from typing import Sequence

from framework import (
    Message, ModelTurn, RunStatus, ToolCall, ToolDefinition, ToolResult,
)
from harness import HarnessConfig, ReActHarness, Skill, SkillRegistry
from harness.react import CLARIFICATION_TOOL_NAME


TOOLS = (
    ToolDefinition("allowed_tool", "Allowed", {"type": "object"}),
    ToolDefinition("other_tool", "Other", {"type": "object"}),
)


def registry(max_steps: int = 5) -> SkillRegistry:
    return SkillRegistry((Skill(
        id="nino-data.test",
        name="Nino Data Test",
        version="1.0.0",
        description="Test skill",
        instructions="Use tools for facts.",
        intent_keywords=("订单",),
        allowed_tools=frozenset({"allowed_tool"}),
        max_steps=max_steps,
        is_default=True,
    ),))


class FakeTools:
    def __init__(self) -> None:
        self.calls: list[ToolCall] = []

    async def list_tools(self) -> Sequence[ToolDefinition]:
        return TOOLS

    async def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(json.dumps({"ok": True, "arguments": call.arguments}))


class MissingTools(FakeTools):
    async def list_tools(self) -> Sequence[ToolDefinition]:
        return ()


class QueueModel:
    def __init__(self, *turns: ModelTurn) -> None:
        self.turns = list(turns)
        self.messages: list[Sequence[Message]] = []

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        self.messages.append(messages)
        return self.turns.pop(0)


class ReActHarnessTests(unittest.IsolatedAsyncioTestCase):
    async def test_calls_tool_then_returns_final_answer(self) -> None:
        model = QueueModel(
            ModelTurn(
                tool_calls=(ToolCall("call-1", "allowed_tool", {"id": "1"}),),
                reasoning_content="Need deterministic data.",
            ),
            ModelTurn(text="Final answer"),
        )
        tools = FakeTools()
        result = await ReActHarness(model, tools, registry()).run("查询订单")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("Final answer", result.answer)
        self.assertEqual(2, result.steps)
        self.assertEqual(1, len(tools.calls))
        self.assertIn("tool_completed", [event.type for event in result.events])
        self.assertEqual("Need deterministic data.", model.messages[1][-2].reasoning_content)
        self.assertEqual("tool", model.messages[1][-1].role)

    async def test_blocks_tool_outside_skill_allowlist(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall("call-1", "other_tool", {}),)))
        result = await ReActHarness(model, FakeTools(), registry()).run("查询订单")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("TOOL_NOT_ALLOWED", result.error_code)

    async def test_blocks_factual_answer_without_tool_observation(self) -> None:
        result = await ReActHarness(
            QueueModel(ModelTurn(text="The order margin is 60.")),
            FakeTools(),
            registry(),
        ).run("查询订单")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("EVIDENCE_REQUIRED", result.error_code)
        self.assertIn("policy_rejected", [event.type for event in result.events])

    async def test_allows_concise_clarification_without_tool_observation(self) -> None:
        result = await ReActHarness(
            QueueModel(ModelTurn(tool_calls=(ToolCall(
                "clarify-1", CLARIFICATION_TOOL_NAME,
                {"message": "请提供需要查询的订单号"},
            ),))),
            FakeTools(),
            registry(),
        ).run("查询订单")

        self.assertEqual(RunStatus.COMPLETED, result.status)
        self.assertEqual("请提供需要查询的订单号", result.answer)
        self.assertIn("clarification_requested", [event.type for event in result.events])

    async def test_blocks_duplicate_tool_call(self) -> None:
        repeated = ToolCall("call-1", "allowed_tool", {"id": "1"})
        model = QueueModel(
            ModelTurn(tool_calls=(repeated,)),
            ModelTurn(tool_calls=(ToolCall("call-2", repeated.name, repeated.arguments),)),
        )
        result = await ReActHarness(model, FakeTools(), registry()).run("查询订单")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("DUPLICATE_TOOL_CALL", result.error_code)

    async def test_stops_at_skill_step_budget(self) -> None:
        model = QueueModel(ModelTurn(tool_calls=(ToolCall("call-1", "allowed_tool", {}),)))
        result = await ReActHarness(
            model,
            FakeTools(),
            registry(max_steps=1),
            HarnessConfig(hard_max_steps=8),
        ).run("查询订单")

        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("MAX_STEPS_EXCEEDED", result.error_code)

    async def test_rejects_empty_input(self) -> None:
        result = await ReActHarness(QueueModel(), FakeTools(), registry()).run("   ")
        self.assertEqual("INVALID_INPUT", result.error_code)

    async def test_reports_missing_required_tool(self) -> None:
        result = await ReActHarness(QueueModel(), MissingTools(), registry()).run("查询订单")
        self.assertEqual(RunStatus.FAILED, result.status)
        self.assertEqual("TOOL_DISCOVERY_ERROR", result.error_code)


class SkillRegistryTests(unittest.TestCase):
    def test_loads_manifest_and_instruction_file(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            folder = Path(root) / "nino-data"
            folder.mkdir()
            (folder / "SKILL.md").write_text(
                "---\nname: Nino Data\ndescription: Test analysis role.\n---\n\nUse approved tools.",
                encoding="utf-8",
            )
            (folder / "skill.json").write_text(json.dumps({
                "id": "nino-data.test",
                "name": "Nino Data",
                "version": "1.0.0",
                "description": "Nino Data test",
                "instructions": "SKILL.md",
                "intent_keywords": ["订单"],
                "excluded_intent_keywords": ["创建订单"],
                "allowed_tools": ["allowed_tool"],
                "max_steps": 5,
                "is_default": True,
            }), encoding="utf-8")

            loaded = SkillRegistry.load(Path(root))
            skill = loaded.route("查询订单")

            self.assertEqual("nino-data.test", skill.id)
            self.assertEqual("Nino Data", skill.name)
            self.assertEqual("Test analysis role.", skill.description)
            self.assertEqual("Use approved tools.", skill.instructions)
            self.assertEqual((skill,), loaded.matches("查询订单"))
            self.assertEqual((), loaded.matches("创建订单"))
            self.assertEqual((), loaded.matches("write a poem"))
            with self.assertRaisesRegex(Exception, "No registered skill matched"):
                loaded.route("write a poem")

    def test_rejects_reference_path_outside_skill_directory(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            root_path = Path(root)
            folder = root_path / "nino-data"
            folder.mkdir()
            (root_path / "outside.md").write_text("secret", encoding="utf-8")
            (folder / "SKILL.md").write_text(
                "---\nname: Nino Data\ndescription: Test.\n---\n\nUse references.",
                encoding="utf-8",
            )
            (folder / "skill.json").write_text(json.dumps({
                "id": "nino-data.test", "version": "1.0.0",
                "instructions": "SKILL.md", "intent_keywords": ["data"],
                "allowed_tools": ["allowed_tool"], "max_steps": 2,
                "references": [{
                    "id": "outside", "path": "../outside.md", "description": "Invalid"
                }],
            }), encoding="utf-8")

            with self.assertRaisesRegex(Exception, "escapes skill directory"):
                SkillRegistry.load(root_path)


if __name__ == "__main__":
    unittest.main()
