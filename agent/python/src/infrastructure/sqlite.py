from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from framework import (
    AgentEvent, AgentRun, Conversation, ConversationContext, ConversationMessage, RunStatus,
)


class SqliteAgentRepository:
    """Local single-instance implementation of the Framework AgentRepository Port."""

    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path.expanduser().resolve()
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._event_conditions: dict[str, asyncio.Condition] = {}
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA busy_timeout = 10000")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS conversations (
            id TEXT PRIMARY KEY,
            title TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS messages (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
            content TEXT NOT NULL,
            run_id TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_messages_conversation_created
            ON messages(conversation_id, created_at, id);
        CREATE TABLE IF NOT EXISTS runs (
            id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            status TEXT NOT NULL,
            skill_id TEXT,
            answer TEXT NOT NULL,
            error_code TEXT,
            steps INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_runs_conversation_created
            ON runs(conversation_id, created_at, id);
        CREATE TABLE IF NOT EXISTS run_events (
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS conversation_contexts (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            through_message_id TEXT NOT NULL,
            compacted_message_count INTEGER NOT NULL,
            original_tokens INTEGER NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        with self._lock, self._connect() as connection:
            connection.executescript(schema)
            context_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(conversation_contexts)")
            }
            if "original_tokens" not in context_columns:
                connection.execute(
                    "ALTER TABLE conversation_contexts "
                    "ADD COLUMN original_tokens INTEGER NOT NULL DEFAULT 0"
                )
            self._recover_interrupted_runs(connection)

    @staticmethod
    def _recover_interrupted_runs(connection: sqlite3.Connection) -> None:
        rows = connection.execute(
            "SELECT id FROM runs WHERE status IN ('queued', 'running')"
        ).fetchall()
        now = datetime.now(timezone.utc).isoformat()
        for row in rows:
            run_id = str(row["id"])
            sequence = connection.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM run_events WHERE run_id = ?",
                (run_id,),
            ).fetchone()[0]
            connection.execute(
                """UPDATE runs
                   SET status = 'failed', error_code = 'RUNTIME_RESTARTED',
                       answer = 'The previous process stopped before this run completed.',
                       completed_at = ?
                   WHERE id = ?""",
                (now, run_id),
            )
            connection.execute(
                "INSERT INTO run_events(run_id, sequence, type, data_json) VALUES (?, ?, ?, ?)",
                (run_id, sequence, "run_failed", json.dumps({
                    "error_code": "RUNTIME_RESTARTED",
                    "message": "The previous process stopped before this run completed.",
                })),
            )

    async def create_conversation(self, conversation: Conversation) -> None:
        await asyncio.to_thread(self._create_conversation, conversation)

    def _create_conversation(self, conversation: Conversation) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO conversations(id, title, created_at, updated_at) VALUES (?, ?, ?, ?)",
                (conversation.id, conversation.title, _time(conversation.created_at),
                 _time(conversation.updated_at)),
            )

    async def get_conversation(self, conversation_id: str) -> Conversation | None:
        return await asyncio.to_thread(self._get_conversation, conversation_id)

    def _get_conversation(self, conversation_id: str) -> Conversation | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversations WHERE id = ?", (conversation_id,)
            ).fetchone()
        return _conversation(row) if row else None

    async def list_conversations(self) -> Sequence[Conversation]:
        return await asyncio.to_thread(self._list_conversations)

    def _list_conversations(self) -> Sequence[Conversation]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM conversations ORDER BY updated_at DESC, id"
            ).fetchall()
        return tuple(_conversation(row) for row in rows)

    async def add_message(self, message: ConversationMessage) -> None:
        await asyncio.to_thread(self._add_message, message)

    def _add_message(self, message: ConversationMessage) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO messages(id, conversation_id, role, content, run_id, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (message.id, message.conversation_id, message.role, message.content,
                 message.run_id, _time(message.created_at)),
            )
            connection.execute(
                "UPDATE conversations SET updated_at = ? WHERE id = ?",
                (_time(message.created_at), message.conversation_id),
            )

    async def list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        return await asyncio.to_thread(self._list_messages, conversation_id)

    def _list_messages(self, conversation_id: str) -> Sequence[ConversationMessage]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM messages WHERE conversation_id = ?
                   ORDER BY created_at, id""",
                (conversation_id,),
            ).fetchall()
        return tuple(_message(row) for row in rows)

    async def get_context(self, conversation_id: str) -> ConversationContext | None:
        return await asyncio.to_thread(self._get_context, conversation_id)

    def _get_context(self, conversation_id: str) -> ConversationContext | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM conversation_contexts WHERE conversation_id = ?",
                (conversation_id,),
            ).fetchone()
        return _context(row) if row else None

    async def upsert_context(self, context: ConversationContext) -> None:
        await asyncio.to_thread(self._upsert_context, context)

    def _upsert_context(self, context: ConversationContext) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO conversation_contexts(
                       conversation_id, summary, through_message_id,
                       compacted_message_count, original_tokens, updated_at
                   ) VALUES (?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       summary = excluded.summary,
                       through_message_id = excluded.through_message_id,
                       compacted_message_count = excluded.compacted_message_count,
                       original_tokens = excluded.original_tokens,
                       updated_at = excluded.updated_at""",
                (context.conversation_id, context.summary, context.through_message_id,
                 context.compacted_message_count, context.original_tokens,
                 _time(context.updated_at)),
            )

    async def create_run(self, run: AgentRun) -> None:
        await asyncio.to_thread(self._create_run, run)
        self._event_conditions.setdefault(run.id, asyncio.Condition())

    def _create_run(self, run: AgentRun) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runs(
                       id, conversation_id, status, skill_id, answer, error_code, steps,
                       created_at, started_at, completed_at, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _run_values(run),
            )

    async def get_run(self, run_id: str) -> AgentRun | None:
        return await asyncio.to_thread(self._get_run, run_id)

    def _get_run(self, run_id: str) -> AgentRun | None:
        with self._lock, self._connect() as connection:
            row = connection.execute("SELECT * FROM runs WHERE id = ?", (run_id,)).fetchone()
        return _run(row) if row else None

    async def list_runs(self, conversation_id: str) -> Sequence[AgentRun]:
        return await asyncio.to_thread(self._list_runs, conversation_id)

    def _list_runs(self, conversation_id: str) -> Sequence[AgentRun]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE conversation_id = ? ORDER BY created_at, id",
                (conversation_id,),
            ).fetchall()
        return tuple(_run(row) for row in rows)

    async def update_run(self, run: AgentRun) -> None:
        await asyncio.to_thread(self._update_run, run)
        await self._notify(run.id)

    def _update_run(self, run: AgentRun) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE runs SET
                       status = ?, skill_id = ?, answer = ?, error_code = ?, steps = ?,
                       created_at = ?, started_at = ?, completed_at = ?, metadata_json = ?
                   WHERE id = ?""",
                (
                    run.status.value, run.skill_id, run.answer, run.error_code, run.steps,
                    _time(run.created_at), _time(run.started_at), _time(run.completed_at),
                    json.dumps(dict(run.metadata), ensure_ascii=False), run.id,
                ),
            )

    async def append_event(self, event: AgentEvent) -> None:
        await asyncio.to_thread(self._append_event, event)
        await self._notify(event.run_id)

    def _append_event(self, event: AgentEvent) -> None:
        with self._lock, self._connect() as connection:
            last = connection.execute(
                "SELECT MAX(sequence) FROM run_events WHERE run_id = ?", (event.run_id,)
            ).fetchone()[0]
            if last is not None and event.sequence <= int(last):
                raise ValueError("Run event sequence must be strictly increasing.")
            connection.execute(
                "INSERT INTO run_events(run_id, sequence, type, data_json) VALUES (?, ?, ?, ?)",
                (event.run_id, event.sequence, event.type,
                 json.dumps(dict(event.data), ensure_ascii=False)),
            )

    async def list_events(self, run_id: str, after: int = 0) -> Sequence[AgentEvent]:
        return await asyncio.to_thread(self._list_events, run_id, after)

    def _list_events(self, run_id: str, after: int) -> Sequence[AgentEvent]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                """SELECT * FROM run_events WHERE run_id = ? AND sequence > ?
                   ORDER BY sequence""",
                (run_id, after),
            ).fetchall()
        return tuple(AgentEvent(
            run_id=str(row["run_id"]), sequence=int(row["sequence"]),
            type=str(row["type"]), data=json.loads(row["data_json"]),
        ) for row in rows)

    async def wait_for_events(
        self, run_id: str, after: int, timeout_seconds: float
    ) -> Sequence[AgentEvent]:
        existing = await self.list_events(run_id, after)
        if existing:
            return existing
        condition = self._event_conditions.setdefault(run_id, asyncio.Condition())
        try:
            async with condition:
                await asyncio.wait_for(condition.wait(), timeout_seconds)
        except TimeoutError:
            return ()
        return await self.list_events(run_id, after)

    async def _notify(self, run_id: str) -> None:
        condition = self._event_conditions.setdefault(run_id, asyncio.Condition())
        async with condition:
            condition.notify_all()


def _time(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _parse_time(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def _conversation(row: sqlite3.Row) -> Conversation:
    return Conversation(
        str(row["id"]), row["title"],
        _parse_time(row["created_at"]), _parse_time(row["updated_at"]),
    )  # type: ignore[arg-type]


def _message(row: sqlite3.Row) -> ConversationMessage:
    return ConversationMessage(
        str(row["id"]), str(row["conversation_id"]), str(row["role"]),
        str(row["content"]), row["run_id"], _parse_time(row["created_at"]),
    )  # type: ignore[arg-type]


def _context(row: sqlite3.Row) -> ConversationContext:
    return ConversationContext(
        str(row["conversation_id"]), str(row["summary"]),
        str(row["through_message_id"]), int(row["compacted_message_count"]),
        int(row["original_tokens"]), _parse_time(row["updated_at"]),
    )  # type: ignore[arg-type]


def _run_values(run: AgentRun) -> tuple[Any, ...]:
    return (
        run.id, run.conversation_id, run.status.value, run.skill_id, run.answer,
        run.error_code, run.steps, _time(run.created_at), _time(run.started_at),
        _time(run.completed_at), json.dumps(dict(run.metadata), ensure_ascii=False),
    )


def _run(row: sqlite3.Row) -> AgentRun:
    return AgentRun(
        id=str(row["id"]), conversation_id=str(row["conversation_id"]),
        status=RunStatus(str(row["status"])), skill_id=row["skill_id"],
        answer=str(row["answer"]), error_code=row["error_code"], steps=int(row["steps"]),
        created_at=_parse_time(row["created_at"]), started_at=_parse_time(row["started_at"]),
        completed_at=_parse_time(row["completed_at"]),
        metadata=json.loads(row["metadata_json"]),
    )  # type: ignore[arg-type]
