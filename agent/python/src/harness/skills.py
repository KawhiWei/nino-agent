from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from framework import LoopBudget
from .documents import DocumentConfigurationError, load_instruction_document


class SkillConfigurationError(ValueError):
    """Raised when a language-neutral shared Skill cannot be loaded safely."""

    pass


@dataclass(frozen=True, slots=True)
class SkillReference:
    id: str
    description: str
    path: Path


@dataclass(frozen=True, slots=True)
class Skill:
    id: str
    name: str
    version: str
    description: str
    instructions: str
    intent_keywords: tuple[str, ...]
    allowed_tools: frozenset[str]
    max_steps: int
    references: tuple[SkillReference, ...] = ()
    is_default: bool = False
    capabilities: tuple[str, ...] = ()
    risk_level: str = "read-only"
    required_evaluators: tuple[str, ...] = ()
    semantic_routing: bool = False
    workflow_id: str = "adaptive"
    workflow_execution_shape: str = "adaptive"
    assurance_mode: str = "best_effort"
    loop_budget: LoopBudget = LoopBudget()


class SkillRegistry:
    def __init__(self, skills: tuple[Skill, ...]) -> None:
        if not skills:
            raise SkillConfigurationError("At least one skill is required.")
        if len({skill.id for skill in skills}) != len(skills):
            raise SkillConfigurationError("Skill ids must be unique.")
        if sum(skill.is_default for skill in skills) > 1:
            raise SkillConfigurationError("Only one default skill is allowed.")
        self._skills = skills

    @property
    def skills(self) -> tuple[Skill, ...]:
        return self._skills

    @classmethod
    def load(cls, root: Path) -> "SkillRegistry":
        if not root.is_dir():
            raise SkillConfigurationError(f"Skill directory does not exist: {root}")
        return cls(tuple(cls._load_skill(path) for path in sorted(root.glob("*/skill.json"))))

    @staticmethod
    def _load_skill(manifest_path: Path) -> Skill:
        try:
            manifest: Mapping[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise SkillConfigurationError(f"Cannot read {manifest_path}: {exc}") from exc
        required = {"id", "version", "instructions", "intent_keywords", "allowed_tools", "max_steps"}
        missing = sorted(required - manifest.keys())
        if missing:
            raise SkillConfigurationError(f"Skill manifest {manifest_path} is missing: {', '.join(missing)}")
        try:
            document = load_instruction_document(manifest_path.parent / str(manifest["instructions"]))
        except DocumentConfigurationError as exc:
            raise SkillConfigurationError(str(exc)) from exc
        max_steps = int(manifest["max_steps"])
        if not 1 <= max_steps <= 20:
            raise SkillConfigurationError("Skill max_steps must be between 1 and 20.")
        keywords = tuple(str(value).strip().lower() for value in manifest["intent_keywords"])
        tools = frozenset(str(value).strip() for value in manifest["allowed_tools"])
        if not all(keywords) or not tools:
            raise SkillConfigurationError("Skill keywords and allowed tools cannot be empty.")
        references = SkillRegistry._load_references(manifest_path, manifest.get("references", []))
        loop_config = manifest.get("loop", {})
        if not isinstance(loop_config, Mapping):
            raise SkillConfigurationError("Skill loop must be an object.")
        try:
            loop_budget = LoopBudget(**loop_config)
        except (TypeError, ValueError) as exc:
            raise SkillConfigurationError(f"Skill loop budget is invalid: {exc}") from exc
        assurance = manifest.get("assurance", {})
        if not isinstance(assurance, Mapping):
            raise SkillConfigurationError("Skill assurance must be an object.")
        required_evaluators = tuple(
            str(item) for item in assurance.get("required_evaluators", ())
        )
        if any(item not in {"verification", "review", "critique"} for item in required_evaluators):
            raise SkillConfigurationError("Skill evaluators must be verification, review, or critique.")
        if len(set(required_evaluators)) != len(required_evaluators):
            raise SkillConfigurationError("Skill evaluators must be unique.")
        assurance_mode = str(assurance.get("mode", "best_effort"))
        if assurance_mode not in {"best_effort", "strict_verify"}:
            raise SkillConfigurationError("Skill assurance mode must be best_effort or strict_verify.")
        routing = manifest.get("routing", {})
        if not isinstance(routing, Mapping):
            raise SkillConfigurationError("Skill routing must be an object.")
        workflow = manifest.get("workflow", {})
        if not isinstance(workflow, Mapping):
            raise SkillConfigurationError("Skill workflow must be an object.")
        workflow_id = str(workflow.get("id", "adaptive")).strip()
        execution_shape = str(workflow.get("execution_shape", "adaptive"))
        if not workflow_id or execution_shape not in {"adaptive", "single_node", "graph"}:
            raise SkillConfigurationError("Skill workflow id or execution shape is invalid.")
        return Skill(
            id=str(manifest["id"]), name=document.name, version=str(manifest["version"]),
            description=document.description, instructions=document.body,
            intent_keywords=keywords, allowed_tools=tools, max_steps=max_steps,
            references=references, is_default=bool(manifest.get("is_default", False)),
            capabilities=tuple(str(item) for item in manifest.get("capabilities", ())),
            risk_level=str(manifest.get("risk_level", "read-only")),
            required_evaluators=required_evaluators,
            semantic_routing=bool(routing.get("semantic_fallback", False)),
            workflow_id=workflow_id,
            workflow_execution_shape=execution_shape,
            assurance_mode=assurance_mode,
            loop_budget=loop_budget,
        )

    @staticmethod
    def _load_references(manifest_path: Path, values: Any) -> tuple[SkillReference, ...]:
        if not isinstance(values, list):
            raise SkillConfigurationError("Skill references must be an array.")
        root = manifest_path.parent.resolve()
        references: list[SkillReference] = []
        for value in values:
            if not isinstance(value, Mapping):
                raise SkillConfigurationError("Each skill reference must be an object.")
            reference_id = str(value.get("id", "")).strip()
            description = str(value.get("description", "")).strip()
            relative = Path(str(value.get("path", "")))
            path = (root / relative).resolve()
            if not reference_id or not description or relative.is_absolute():
                raise SkillConfigurationError("Reference id, description, and relative path are required.")
            if path.parent != root and root not in path.parents:
                raise SkillConfigurationError(f"Reference path escapes skill directory: {relative}")
            if not path.is_file():
                raise SkillConfigurationError(f"Reference file does not exist: {path}")
            references.append(SkillReference(reference_id, description, path))
        if len({item.id for item in references}) != len(references):
            raise SkillConfigurationError("Reference ids must be unique within a skill.")
        return tuple(references)

    def get(self, skill_id: str) -> Skill:
        match = next((skill for skill in self._skills if skill.id == skill_id), None)
        if match is None:
            raise SkillConfigurationError(f"Unknown skill: {skill_id}")
        return match

    def matches(self, user_input: str) -> tuple[Skill, ...]:
        """Return only explicitly matched Skills, ordered by keyword evidence."""

        normalized = user_input.lower()
        ranked = sorted(
            (
                (
                    sum(normalized.count(keyword) for keyword in skill.intent_keywords)
                ),
                skill.id,
                skill,
            )
            for skill in self._skills
        )
        return tuple(
            item[2]
            for item in sorted(ranked, key=lambda item: (-item[0], item[1]))
            if item[0] > 0
        )

    def route(self, user_input: str) -> Skill:
        matches = self.matches(user_input)
        if matches:
            return matches[0]
        raise SkillConfigurationError("No registered skill matched the user input.")

    def semantic_candidates(self, user_input: str) -> tuple[Skill, ...]:
        """Return Skills that opt in to semantic capability matching."""

        return tuple(
            skill for skill in self._skills
            if skill.semantic_routing
        )
