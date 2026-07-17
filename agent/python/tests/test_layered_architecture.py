from __future__ import annotations

import unittest
import ast
from pathlib import Path
from typing import Sequence

from framework import (
    HarnessStepState,
    Message,
    ModelTurn,
    ToolCall,
    ToolDefinition,
    ToolResult,
)
from harness import ReActHarness, Skill, SkillRegistry


class OneTurnModel:
    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        return ModelTurn(text=f"messages={len(messages)},tools={len(tools)}")


class EmptyToolProvider:
    async def list_tools(self) -> Sequence[ToolDefinition]:
        return ()

    async def invoke(self, call: ToolCall) -> ToolResult:
        raise AssertionError(f"Unexpected tool call: {call.name}")


class LayeredArchitectureTests(unittest.IsolatedAsyncioTestCase):
    def test_removed_compatibility_modules_stay_removed(self) -> None:
        package = Path(__file__).resolve().parents[1] / "src"
        removed = (
            "nino_agent_runtime", "application",
            "models.py", "ports.py", "skills.py", "infrastructure/mcp_http.py",
        )
        for relative_path in removed:
            self.assertFalse((package / relative_path).exists(), relative_path)

    async def test_harness_step_is_one_framework_model_decision(self) -> None:
        skills = SkillRegistry((Skill(
            id="nino-data.test",
            name="test",
            version="1.0.0",
            description="test",
            instructions="Answer directly.",
            intent_keywords=("test",),
            allowed_tools=frozenset({"unused"}),
            max_steps=2,
            is_default=True,
        ),))
        harness = ReActHarness(OneTurnModel(), EmptyToolProvider(), skills)

        result = await harness.step(HarnessStepState(
            messages=(Message(role="user", content="test"),),
            tools=(),
            step=1,
            max_steps=2,
        ))

        self.assertEqual("messages=1,tools=0", result.text)

    def test_framework_and_harness_do_not_import_infrastructure(self) -> None:
        package = Path(__file__).resolve().parents[1] / "src"
        for layer in ("framework", "harness"):
            for path in (package / layer).glob("*.py"):
                source = path.read_text(encoding="utf-8")
                imports = []
                for node in ast.walk(ast.parse(source)):
                    if isinstance(node, ast.Import):
                        imports.extend(alias.name for alias in node.names)
                    elif isinstance(node, ast.ImportFrom):
                        imports.append(node.module or "")
                for imported in imports:
                    self.assertNotIn(
                        "infrastructure", imported, f"Invalid dependency in {path}"
                    )
                    self.assertNotIn("api", imported, f"Invalid dependency in {path}")


if __name__ == "__main__":
    unittest.main()
