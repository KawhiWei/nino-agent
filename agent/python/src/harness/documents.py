from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

import yaml


class DocumentConfigurationError(ValueError):
    """Raised when shared Markdown frontmatter or instructions are invalid."""

    pass


@dataclass(frozen=True, slots=True)
class InstructionDocument:
    name: str
    description: str
    body: str


def load_instruction_document(path: Path) -> InstructionDocument:
    try:
        content = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise DocumentConfigurationError(f"Cannot read {path}: {exc}") from exc
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise DocumentConfigurationError(f"Instruction file must start with frontmatter: {path}")
    try:
        end = next(index for index, line in enumerate(lines[1:], 1) if line.strip() == "---")
    except StopIteration as exc:
        raise DocumentConfigurationError(f"Frontmatter is not closed in {path}") from exc
    try:
        metadata = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError as exc:
        raise DocumentConfigurationError(f"Invalid frontmatter in {path}: {exc}") from exc
    if not isinstance(metadata, Mapping):
        raise DocumentConfigurationError(f"Frontmatter must be a mapping in {path}")
    name = _required_text(metadata, "name", path)
    description = _required_text(metadata, "description", path)
    body = "\n".join(lines[end + 1:]).strip()
    if not body:
        raise DocumentConfigurationError(f"Instruction body cannot be empty: {path}")
    return InstructionDocument(name, description, body)


def _required_text(metadata: Mapping[str, Any], key: str, path: Path) -> str:
    value = metadata.get(key)
    if not isinstance(value, str) or not value.strip():
        raise DocumentConfigurationError(f"Frontmatter {key} is required in {path}")
    return value.strip()
