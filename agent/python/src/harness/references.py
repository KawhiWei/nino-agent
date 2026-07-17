from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from framework import ToolCall, ToolDefinition, ToolResult
from .skills import Skill


REFERENCE_TOOL_NAME = "nino_runtime_load_reference"


@dataclass(frozen=True, slots=True)
class LoadedReference:
    id: str
    description: str
    content: str
    sha256: str


class ReferenceProvider:
    def __init__(self, max_chars: int = 20_000) -> None:
        if max_chars < 1:
            raise ValueError("max_chars must be positive.")
        self._max_chars = max_chars

    def tool_definition(self, skill: Skill) -> ToolDefinition:
        return ToolDefinition(
            REFERENCE_TOOL_NAME,
            "Load one approved detailed reference for the active Skill only when needed.",
            {
                "type": "object",
                "properties": {
                    "reference_id": {
                        "type": "string",
                        "enum": [item.id for item in skill.references],
                        "description": "; ".join(
                            f"{item.id}: {item.description}" for item in skill.references
                        ),
                    }
                },
                "required": ["reference_id"],
                "additionalProperties": False,
            },
        )

    def load(self, skill: Skill, reference_id: str) -> LoadedReference:
        reference = next((item for item in skill.references if item.id == reference_id), None)
        if reference is None:
            raise ValueError(f"Reference is not allowed by skill {skill.id}: {reference_id}")
        content = reference.path.read_text(encoding="utf-8")
        if len(content) > self._max_chars:
            raise ValueError(f"Reference exceeds {self._max_chars} characters: {reference_id}")
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        return LoadedReference(reference.id, reference.description, content, digest)

    def invoke(self, skill: Skill, call: ToolCall) -> tuple[ToolResult, LoadedReference]:
        reference_id = str(call.arguments.get("reference_id", "")).strip()
        loaded = self.load(skill, reference_id)
        return ToolResult(json.dumps({
            "reference_id": loaded.id,
            "description": loaded.description,
            "sha256": loaded.sha256,
            "content": loaded.content,
        }, ensure_ascii=False)), loaded
