from __future__ import annotations

import asyncio
from dataclasses import replace
from typing import Sequence

from framework import (
    AgentEvent, AgentRun, Conversation, ConversationContext, ConversationMessage,
)


class InMemoryAgentRepository:
    def __init__(self) -> None:
        self._conversations: dict[str, Conversation] = {}
        self._messages: dict[str, list[ConversationMessage]] = {}
        self._runs: dict[str, AgentRun] = {}
        self._events: dict[str, list[AgentEvent]] = {}
        self._contexts: dict[str, ConversationContext] = {}
        self._event_conditions: dict[str, asyncio.Condition] = {}
        self._lock = asyncio.Lock()

    async def create_conversation(self, conversation: Conversation) -> None:
        async with self._lock:
            self._conversations[conversation.id] = conversation
            self._messages[conversation.id] = []

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        async with self._lock:
            return self._conversations.get(conversation_id)

    async def list_conversations(self) -> Sequence[Conversation]:
        async with self._lock:
            return tuple(sorted(
                self._conversations.values(), key=lambda item: item.updated_at, reverse=True
            ))

    async def add_message(self, message: ConversationMessage) -> None:
        async with self._lock:
            self._messages.setdefault(message.conversation_id, []).append(message)
            conversation = self._conversations.get(message.conversation_id)
            if conversation is not None:
                self._conversations[message.conversation_id] = replace(
                    conversation, updated_at=message.created_at
                )

    async def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        async with self._lock:
            return tuple(self._messages.get(conversation_id, ()))

    async def get_context(self, conversation_id: str) -> ConversationContext | None:
        async with self._lock:
            return self._contexts.get(conversation_id)

    async def upsert_context(self, context: ConversationContext) -> None:
        async with self._lock:
            self._contexts[context.conversation_id] = context

    async def create_run(self, run: AgentRun) -> None:
        async with self._lock:
            self._runs[run.id] = replace(run)
            self._events[run.id] = []
            self._event_conditions[run.id] = asyncio.Condition()

    async def get_run(self, run_id: str) -> AgentRun | None:
        async with self._lock:
            run = self._runs.get(run_id)
            return replace(run) if run else None

    async def list_runs(self, conversation_id: str) -> Sequence[AgentRun]:
        async with self._lock:
            return tuple(
                replace(run)
                for run in sorted(self._runs.values(), key=lambda item: item.created_at)
                if run.conversation_id == conversation_id
            )

    async def update_run(self, run: AgentRun) -> None:
        async with self._lock:
            self._runs[run.id] = replace(run)
        condition = self._event_conditions.get(run.id)
        if condition is not None:
            async with condition:
                condition.notify_all()

    async def append_event(self, event: AgentEvent) -> None:
        async with self._lock:
            events = self._events.setdefault(event.run_id, [])
            if events and event.sequence <= events[-1].sequence:
                raise ValueError("Run event sequence must be strictly increasing.")
            events.append(event)
        condition = self._event_conditions.get(event.run_id)
        if condition is not None:
            async with condition:
                condition.notify_all()

    async def list_events(self, run_id: str, after: int = 0) -> Sequence[AgentEvent]:
        async with self._lock:
            return tuple(event for event in self._events.get(run_id, ()) if event.sequence > after)

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float
    ) -> Sequence[AgentEvent]:
        existing = await self.list_events(run_id, after)
        if existing:
            return existing
        condition = self._event_conditions.get(run_id)
        if condition is None:
            return ()
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout_seconds)
        except TimeoutError:
            return ()
        return await self.list_events(run_id, after)
