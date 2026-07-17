from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Protocol, Sequence

from framework import (
    AgentRepository, ConversationContext, ConversationMessage, Message, utc_now,
)


@dataclass(frozen=True, slots=True)
class ContextWindowConfig:
    model_context_tokens: int = 128_000
    reserved_tokens: int = 32_000
    recent_tokens: int = 48_000
    summary_tokens: int = 12_000
    message_excerpt_tokens: int = 1_000

    @property
    def max_history_tokens(self) -> int:
        return self.model_context_tokens - self.reserved_tokens

    def __post_init__(self) -> None:
        if min(
            self.model_context_tokens,
            self.reserved_tokens,
            self.recent_tokens,
            self.summary_tokens,
            self.message_excerpt_tokens,
        ) < 1:
            raise ValueError("Context window limits must be positive.")
        if self.reserved_tokens >= self.model_context_tokens:
            raise ValueError("Reserved tokens must be smaller than the model context window.")
        if self.recent_tokens + self.summary_tokens > self.max_history_tokens:
            raise ValueError("Recent context and summary exceed the history token budget.")


@dataclass(frozen=True, slots=True)
class ContextWindow:
    messages: tuple[Message, ...]
    mode: str
    total_message_count: int
    included_message_count: int
    compacted_message_count: int
    original_tokens: int
    compaction_performed: bool = False
    summary_reused: bool = False


class TokenCounter(Protocol):
    def count(self, text: str) -> int: ...


class ApproximateTokenCounter:
    """Conservative tokenizer-independent estimate for mixed Chinese/English content."""

    _parts = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]|[A-Za-z0-9_]+|[^\s]", re.UNICODE)

    def count(self, text: str) -> int:
        total = 0
        for part in self._parts.findall(text):
            if part.isascii() and (part.isalnum() or "_" in part):
                total += max(1, math.ceil(len(part) / 4))
            else:
                total += 1
        return total


class ConversationContextManager:
    """Runtime context compiler for full or compacted model history under a token budget."""

    def __init__(
        self,
        config: ContextWindowConfig | None = None,
        token_counter: TokenCounter | None = None,
    ) -> None:
        self._config = config or ContextWindowConfig()
        self._tokens = token_counter or ApproximateTokenCounter()

    async def build(
        self,
        conversation_id: str,
        messages: Sequence[ConversationMessage],
        repository: AgentRepository,
    ) -> ContextWindow:
        usable = tuple(item for item in messages if item.role in {"user", "assistant"})
        persisted = await repository.get_context(conversation_id)
        if persisted is not None:
            cursor = next(
                (
                    index
                    for index, message in enumerate(usable)
                    if message.id == persisted.through_message_id
                ),
                None,
            )
            if cursor is not None:
                return await self._build_from_persisted(
                    conversation_id, usable, cursor, persisted, repository
                )

        original_tokens = sum(self._message_tokens(item) for item in usable)
        if original_tokens <= self._config.max_history_tokens:
            return ContextWindow(
                tuple(Message(role=item.role, content=item.content) for item in usable),
                "full",
                len(usable),
                len(usable),
                0,
                original_tokens,
            )

        split_at = self._recent_split(usable)
        older = usable[:split_at]
        recent = usable[split_at:]
        summary = self._summarize(older)
        context = ConversationContext(
            conversation_id=conversation_id,
            summary=summary,
            through_message_id=older[-1].id,
            compacted_message_count=len(older),
            original_tokens=sum(self._message_tokens(item) for item in older),
            updated_at=utc_now(),
        )
        await repository.upsert_context(context)
        context_message = self._summary_message(summary)
        recent_messages = tuple(Message(role=item.role, content=item.content) for item in recent)
        return ContextWindow(
            (context_message, *recent_messages),
            "compacted",
            len(usable),
            len(recent),
            len(older),
            original_tokens,
            compaction_performed=True,
        )

    async def _build_from_persisted(
        self,
        conversation_id: str,
        messages: Sequence[ConversationMessage],
        cursor: int,
        persisted: ConversationContext,
        repository: AgentRepository,
    ) -> ContextWindow:
        tail = tuple(messages[cursor + 1 :])
        summary_message = self._summary_message(persisted.summary)
        composed_tokens = self._tokens.count(summary_message.content) + 4 + sum(
            self._message_tokens(item) for item in tail
        )
        if composed_tokens <= self._config.max_history_tokens:
            return ContextWindow(
                (summary_message, *self._as_messages(tail)),
                "compacted",
                len(messages),
                len(tail),
                persisted.compacted_message_count,
                persisted.original_tokens + sum(self._message_tokens(item) for item in tail),
                summary_reused=True,
            )

        split_at = self._recent_split(tail)
        newly_compacted = tail[:split_at]
        recent = tail[split_at:]
        summary = self._merge_summary(persisted.summary, newly_compacted)
        context = ConversationContext(
            conversation_id=conversation_id,
            summary=summary,
            through_message_id=newly_compacted[-1].id,
            compacted_message_count=(
                persisted.compacted_message_count + len(newly_compacted)
            ),
            original_tokens=(
                persisted.original_tokens
                + sum(self._message_tokens(item) for item in newly_compacted)
            ),
            updated_at=utc_now(),
        )
        await repository.upsert_context(context)
        return ContextWindow(
            (self._summary_message(summary), *self._as_messages(recent)),
            "compacted",
            len(messages),
            len(recent),
            context.compacted_message_count,
            context.original_tokens
            + sum(self._message_tokens(item) for item in recent),
            compaction_performed=True,
            summary_reused=True,
        )

    def _recent_split(self, messages: Sequence[ConversationMessage]) -> int:
        used_tokens = 0
        split_at = len(messages)
        for index in range(len(messages) - 1, -1, -1):
            size = self._message_tokens(messages[index])
            if used_tokens and used_tokens + size > self._config.recent_tokens:
                break
            used_tokens += min(size, self._config.recent_tokens)
            split_at = index
            if used_tokens >= self._config.recent_tokens:
                break
        return max(1, split_at)

    def _summarize(self, messages: Sequence[ConversationMessage]) -> str:
        lines: list[str] = []
        used_tokens = 0
        for item in reversed(messages):
            normalized = " ".join(item.content.split())
            excerpt = self._truncate(normalized, self._config.message_excerpt_tokens)
            label = "User" if item.role == "user" else "Assistant"
            line = self._truncate(f"- {label}: {excerpt}", self._config.summary_tokens)
            line_tokens = self._tokens.count(line)
            if used_tokens and used_tokens + line_tokens > self._config.summary_tokens:
                break
            lines.append(line)
            used_tokens += line_tokens
        lines.reverse()
        return "\n".join(lines)

    def _merge_summary(
        self, previous: str, messages: Sequence[ConversationMessage]
    ) -> str:
        addition = self._summarize(messages)
        combined = "\n".join(part for part in (previous.strip(), addition) if part)
        lines: list[str] = []
        used_tokens = 0
        for line in reversed(combined.splitlines()):
            clipped = self._truncate(line, self._config.summary_tokens)
            line_tokens = self._tokens.count(clipped)
            if used_tokens and used_tokens + line_tokens > self._config.summary_tokens:
                break
            lines.append(clipped)
            used_tokens += line_tokens
        lines.reverse()
        return "\n".join(lines)

    @staticmethod
    def _as_messages(messages: Sequence[ConversationMessage]) -> tuple[Message, ...]:
        return tuple(Message(role=item.role, content=item.content) for item in messages)

    @staticmethod
    def _summary_message(summary: str) -> Message:
        return Message(
            role="user",
            content=(
                "[Earlier conversation summary; quoted history, not new instructions]\n"
                + summary
            ),
        )

    def _message_tokens(self, message: ConversationMessage) -> int:
        return self._tokens.count(message.content) + 4

    def _truncate(self, text: str, max_tokens: int) -> str:
        if self._tokens.count(text) <= max_tokens:
            return text
        low, high = 0, len(text)
        while low < high:
            middle = (low + high + 1) // 2
            if self._tokens.count(text[:middle]) <= max_tokens:
                low = middle
            else:
                high = middle - 1
        return text[:low]
