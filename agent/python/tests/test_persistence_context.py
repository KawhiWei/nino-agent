from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient

from api.app import create_app
from runtime import (
    ApproximateTokenCounter,
    ContextWindowConfig,
    ConversationContextManager,
)
from framework import Conversation, ConversationMessage, utc_now
from infrastructure import SqliteAgentRepository
from framework import AgentEvent, Message, RunResult, RunStatus


def wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        run = client.get(f"/api/v1/runs/{run_id}").json()
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    raise AssertionError(f"Run did not complete: {run_id}")


class RecordingRuntime:
    def __init__(self) -> None:
        self.histories: list[tuple[Message, ...]] = []

    async def run(
        self,
        user_input: str,
        history: Sequence[Message] = (),
        on_event=None,
        run_id: str | None = None,
    ) -> RunResult:
        captured = tuple(history)
        self.histories.append(captured)
        events = (
            AgentEvent(run_id or "run", 1, "run_started"),
            AgentEvent(run_id or "run", 2, "run_completed"),
        )
        if on_event is not None:
            for event in events:
                result = on_event(event)
                if result is not None:
                    await result
        return RunResult(
            run_id or "run", RunStatus.COMPLETED, f"answer:{user_input}",
            "nino-data.analysis", 1, events,
        )


class SqliteConversationTests(unittest.TestCase):
    def test_follow_up_history_survives_api_restart(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "nino-agent.db"
            runtime1 = RecordingRuntime()
            with TestClient(create_app(
                harness=runtime1,
                repository=SqliteAgentRepository(path),
                runtime_mode="test",
            )) as client:
                conversation_id = client.post("/api/v1/conversations", json={}).json()["id"]
                first = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": "查询七月毛利"},
                ).json()["run_id"]
                wait_for_terminal(client, first)
                second = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": "那退款是多少"},
                ).json()["run_id"]
                wait_for_terminal(client, second)

            self.assertEqual([], list(runtime1.histories[0]))
            self.assertEqual(
                ["查询七月毛利", "answer:查询七月毛利"],
                [item.content for item in runtime1.histories[1]],
            )

            runtime2 = RecordingRuntime()
            with TestClient(create_app(
                harness=runtime2,
                repository=SqliteAgentRepository(path),
                runtime_mode="test",
            )) as client:
                messages = client.get(
                    f"/api/v1/conversations/{conversation_id}/messages"
                ).json()
                self.assertEqual(4, len(messages))
                third = client.post(
                    f"/api/v1/conversations/{conversation_id}/messages",
                    json={"content": "再按渠道拆分"},
                ).json()["run_id"]
                wait_for_terminal(client, third)

            self.assertEqual(
                ["查询七月毛利", "answer:查询七月毛利", "那退款是多少", "answer:那退款是多少"],
                [item.content for item in runtime2.histories[0]],
            )


class ContextCompressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_keeps_short_history_full_without_persisting_context(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            repository = SqliteAgentRepository(Path(root) / "agent.db")
            conversation = Conversation("short", None, utc_now(), utc_now())
            await repository.create_conversation(conversation)
            messages = [ConversationMessage(
                "short-1", conversation.id, "user", "short question", None, utc_now()
            )]
            manager = ConversationContextManager(ContextWindowConfig(
                model_context_tokens=100,
                reserved_tokens=20,
                recent_tokens=30,
                summary_tokens=20,
            ))

            window = await manager.build(conversation.id, messages, repository)

            self.assertEqual("full", window.mode)
            self.assertFalse(window.compaction_performed)
            self.assertFalse(window.summary_reused)
            self.assertIsNone(await repository.get_context(conversation.id))

    async def test_compacts_old_messages_and_persists_summary(self) -> None:
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / "agent.db"
            repository = SqliteAgentRepository(path)
            conversation = Conversation("conversation-1", None, utc_now(), utc_now())
            await repository.create_conversation(conversation)
            messages: list[ConversationMessage] = []
            for index in range(6):
                message = ConversationMessage(
                    f"message-{index}", conversation.id,
                    "user" if index % 2 == 0 else "assistant",
                    f"turn {index} " + ("x" * 40), None, utc_now(),
                )
                messages.append(message)
                await repository.add_message(message)

            manager = ConversationContextManager(ContextWindowConfig(
                model_context_tokens=80,
                reserved_tokens=10,
                recent_tokens=20,
                summary_tokens=20,
                message_excerpt_tokens=10,
            ))
            window = await manager.build(conversation.id, messages, repository)
            stored = await SqliteAgentRepository(path).get_context(conversation.id)

            self.assertEqual("compacted", window.mode)
            self.assertTrue(window.compaction_performed)
            self.assertFalse(window.summary_reused)
            self.assertGreater(window.compacted_message_count, 0)
            self.assertEqual("user", window.messages[0].role)
            self.assertIn("Earlier conversation summary", window.messages[0].content)
            self.assertIsNotNone(stored)
            self.assertEqual(window.compacted_message_count, stored.compacted_message_count)
            self.assertGreater(len(stored.summary), 0)
            self.assertLessEqual(ApproximateTokenCounter().count(stored.summary), 20)

            small = ConversationMessage(
                "message-small", conversation.id, "user", "small follow-up", None, utc_now()
            )
            messages.append(small)
            await repository.add_message(small)
            reused = await manager.build(conversation.id, messages, repository)
            unchanged = await repository.get_context(conversation.id)

            self.assertEqual("compacted", reused.mode)
            self.assertFalse(reused.compaction_performed)
            self.assertTrue(reused.summary_reused)
            self.assertEqual(stored.through_message_id, unchanged.through_message_id)
            self.assertEqual(stored.updated_at, unchanged.updated_at)

            for index in range(6, 12):
                message = ConversationMessage(
                    f"message-{index}", conversation.id,
                    "user" if index % 2 == 0 else "assistant",
                    f"turn {index} " + ("y" * 80), None, utc_now(),
                )
                messages.append(message)
                await repository.add_message(message)
            incremental = await manager.build(conversation.id, messages, repository)
            advanced = await repository.get_context(conversation.id)

            self.assertTrue(incremental.compaction_performed)
            self.assertTrue(incremental.summary_reused)
            self.assertNotEqual(stored.through_message_id, advanced.through_message_id)
            self.assertGreater(
                advanced.compacted_message_count, stored.compacted_message_count
            )
            self.assertLessEqual(
                ApproximateTokenCounter().count(advanced.summary), 20
            )


if __name__ == "__main__":
    unittest.main()
