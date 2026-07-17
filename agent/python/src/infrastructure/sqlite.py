from __future__ import annotations

import asyncio
import json
import sqlite3
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Sequence
from uuid import uuid4

from framework import (
    AcceptanceContract, ActiveRunConflictError, AgentEvent, AgentRun, AttemptStatus, Conversation,
    ConversationContext, ConversationMessage, GateStatus, NodeAttempt, RunStatus,
    TaskGate, TaskGraph, TaskGraphSnapshot, TaskGraphStatus, TaskNode, TaskNodeStatus,
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
            metadata_json TEXT NOT NULL,
            parent_graph_id TEXT,
            relation_type TEXT,
            archived_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_runs_conversation_created
            ON runs(conversation_id, created_at, id);
        CREATE UNIQUE INDEX IF NOT EXISTS ux_runs_one_active_per_conversation
            ON runs(conversation_id) WHERE status IN ('queued', 'running');
        CREATE TABLE IF NOT EXISTS run_events (
            run_id TEXT NOT NULL REFERENCES runs(id) ON DELETE CASCADE,
            sequence INTEGER NOT NULL,
            type TEXT NOT NULL,
            data_json TEXT NOT NULL,
            PRIMARY KEY (run_id, sequence)
        );
        CREATE TABLE IF NOT EXISTS run_event_counters (
            run_id TEXT PRIMARY KEY REFERENCES runs(id) ON DELETE CASCADE,
            last_sequence INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS runtime_instances (
            id TEXT PRIMARY KEY,
            status TEXT NOT NULL,
            heartbeat_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS conversation_contexts (
            conversation_id TEXT PRIMARY KEY REFERENCES conversations(id) ON DELETE CASCADE,
            summary TEXT NOT NULL,
            through_message_id TEXT NOT NULL,
            compacted_message_count INTEGER NOT NULL,
            original_tokens INTEGER NOT NULL,
            updated_at TEXT NOT NULL,
            algorithm_version TEXT NOT NULL DEFAULT 'extractive-v1',
            token_counter TEXT NOT NULL DEFAULT 'approximate-v1'
        );
        CREATE TABLE IF NOT EXISTS task_graphs (
            id TEXT PRIMARY KEY,
            run_id TEXT NOT NULL UNIQUE REFERENCES runs(id) ON DELETE CASCADE,
            conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
            user_intent TEXT NOT NULL,
            status TEXT NOT NULL,
            version INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            completed_at TEXT,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_task_graphs_status_updated
            ON task_graphs(status, updated_at);
        CREATE TABLE IF NOT EXISTS task_nodes (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
            parent_node_id TEXT,
            kind TEXT NOT NULL,
            owner_agent_id TEXT NOT NULL,
            title TEXT NOT NULL,
            status TEXT NOT NULL,
            dependencies_json TEXT NOT NULL,
            contract_json TEXT NOT NULL,
            result_summary TEXT NOT NULL,
            result_json TEXT NOT NULL DEFAULT '{}',
            error_code TEXT,
            created_at TEXT NOT NULL,
            started_at TEXT,
            completed_at TEXT,
            metadata_json TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS ix_task_nodes_graph_status
            ON task_nodes(graph_id, status, created_at);
        CREATE TABLE IF NOT EXISTS task_gates (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
            kind TEXT NOT NULL,
            status TEXT NOT NULL,
            required INTEGER NOT NULL,
            evaluator_agent_id TEXT,
            verdict TEXT NOT NULL,
            evidence_json TEXT NOT NULL,
            created_at TEXT NOT NULL,
            evaluated_at TEXT
        );
        CREATE INDEX IF NOT EXISTS ix_task_gates_graph_status
            ON task_gates(graph_id, status, created_at);
        CREATE TABLE IF NOT EXISTS node_attempts (
            id TEXT PRIMARY KEY,
            graph_id TEXT NOT NULL REFERENCES task_graphs(id) ON DELETE CASCADE,
            node_id TEXT NOT NULL REFERENCES task_nodes(id) ON DELETE CASCADE,
            attempt_number INTEGER NOT NULL,
            status TEXT NOT NULL,
            lease_owner TEXT,
            lease_expires_at TEXT,
            started_at TEXT NOT NULL,
            completed_at TEXT,
            error_code TEXT,
            checkpoint_json TEXT NOT NULL,
            UNIQUE(node_id, attempt_number)
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
            if "algorithm_version" not in context_columns:
                connection.execute(
                    "ALTER TABLE conversation_contexts ADD COLUMN "
                    "algorithm_version TEXT NOT NULL DEFAULT 'extractive-v1'"
                )
            if "token_counter" not in context_columns:
                connection.execute(
                    "ALTER TABLE conversation_contexts ADD COLUMN "
                    "token_counter TEXT NOT NULL DEFAULT 'approximate-v1'"
                )
            attempt_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(node_attempts)")
            }
            if "lease_expires_at" not in attempt_columns:
                connection.execute("ALTER TABLE node_attempts ADD COLUMN lease_expires_at TEXT")
            node_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(task_nodes)")
            }
            if "result_json" not in node_columns:
                connection.execute(
                    "ALTER TABLE task_nodes ADD COLUMN result_json TEXT NOT NULL DEFAULT '{}'"
                )
            graph_columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(task_graphs)")
            }
            for name, definition in (
                ("parent_graph_id", "TEXT"),
                ("relation_type", "TEXT"),
                ("archived_at", "TEXT"),
            ):
                if name not in graph_columns:
                    connection.execute(
                        f"ALTER TABLE task_graphs ADD COLUMN {name} {definition}"
                    )
            connection.execute(
                """INSERT OR IGNORE INTO run_event_counters(run_id, last_sequence)
                   SELECT id, COALESCE((
                       SELECT MAX(sequence) FROM run_events WHERE run_events.run_id = runs.id
                   ), 0) FROM runs"""
            )

    async def register_runtime(self, runtime_id: str) -> None:
        await asyncio.to_thread(self._set_runtime_state, runtime_id, "active")

    async def heartbeat_runtime(self, runtime_id: str) -> None:
        await asyncio.to_thread(self._set_runtime_state, runtime_id, "active")

    async def unregister_runtime(self, runtime_id: str) -> None:
        await asyncio.to_thread(self._set_runtime_state, runtime_id, "stopped")

    def _set_runtime_state(self, runtime_id: str, status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO runtime_instances(id, status, heartbeat_at) VALUES (?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET status = excluded.status,
                       heartbeat_at = excluded.heartbeat_at""",
                (runtime_id, status, now),
            )

    async def prepare_recovery(self, runtime_id: str, stale_after_seconds: int) -> None:
        await asyncio.to_thread(
            self._prepare_recovery, runtime_id, stale_after_seconds
        )

    def _prepare_recovery(self, runtime_id: str, stale_after_seconds: int) -> None:
        now = datetime.now(timezone.utc)
        stale_before = (now - timedelta(seconds=stale_after_seconds)).isoformat()
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            stale_attempts = connection.execute(
                """SELECT a.id, a.node_id, a.graph_id
                   FROM node_attempts a
                   LEFT JOIN runtime_instances r ON r.id = a.lease_owner
                   WHERE a.status = 'running'
                     AND a.lease_owner <> ?
                     AND (r.id IS NULL OR r.status <> 'active' OR r.heartbeat_at < ?
                          OR a.lease_expires_at IS NULL OR a.lease_expires_at < ?)""",
                (runtime_id, stale_before, now.isoformat()),
            ).fetchall()
            if not stale_attempts:
                return
            attempt_ids = [str(row["id"]) for row in stale_attempts]
            node_ids = [str(row["node_id"]) for row in stale_attempts]
            graph_ids = [str(row["graph_id"]) for row in stale_attempts]
            placeholders = ",".join("?" for _ in attempt_ids)
            connection.execute(
                f"""UPDATE node_attempts SET status = 'interrupted', completed_at = ?,
                       error_code = 'RUNTIME_RESTARTED', lease_owner = NULL,
                       lease_expires_at = NULL WHERE id IN ({placeholders})""",
                (now.isoformat(), *attempt_ids),
            )
            placeholders = ",".join("?" for _ in node_ids)
            connection.execute(
                f"""UPDATE task_nodes SET status = 'pending', started_at = NULL,
                       completed_at = NULL, error_code = NULL
                   WHERE id IN ({placeholders})""",
                node_ids,
            )
            placeholders = ",".join("?" for _ in graph_ids)
            connection.execute(
                f"""UPDATE task_graphs SET status = 'pending', updated_at = ?, completed_at = NULL
                   WHERE id IN ({placeholders})""",
                (now.isoformat(), *graph_ids),
            )
            connection.execute(
                f"""UPDATE runs SET status = 'queued', error_code = NULL, answer = '',
                       started_at = NULL, completed_at = NULL
                   WHERE id IN (SELECT run_id FROM task_graphs WHERE id IN ({placeholders}))""",
                graph_ids,
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
                       compacted_message_count, original_tokens, updated_at,
                       algorithm_version, token_counter
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(conversation_id) DO UPDATE SET
                       summary = excluded.summary,
                       through_message_id = excluded.through_message_id,
                       compacted_message_count = excluded.compacted_message_count,
                       original_tokens = excluded.original_tokens,
                       updated_at = excluded.updated_at,
                       algorithm_version = excluded.algorithm_version,
                       token_counter = excluded.token_counter""",
                (context.conversation_id, context.summary, context.through_message_id,
                 context.compacted_message_count, context.original_tokens,
                 _time(context.updated_at), context.algorithm_version, context.token_counter),
            )

    async def create_run(self, run: AgentRun) -> None:
        await asyncio.to_thread(self._create_run, run)
        self._event_conditions.setdefault(run.id, asyncio.Condition())

    async def create_run_with_message(
        self, run: AgentRun, message: ConversationMessage
    ) -> None:
        await asyncio.to_thread(self._create_run_with_message, run, message)
        self._event_conditions.setdefault(run.id, asyncio.Condition())

    def _create_run_with_message(
        self, run: AgentRun, message: ConversationMessage
    ) -> None:
        try:
            with self._lock, self._connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO runs(
                           id, conversation_id, status, skill_id, answer, error_code, steps,
                           created_at, started_at, completed_at, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _run_values(run),
                )
                connection.execute(
                    "INSERT INTO run_event_counters(run_id, last_sequence) VALUES (?, 0)",
                    (run.id,),
                )
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
        except sqlite3.IntegrityError as exc:
            if "runs.conversation_id" in str(exc):
                raise ActiveRunConflictError(
                    f"Conversation already has an active Run: {run.conversation_id}"
                ) from exc
            raise

    def _create_run(self, run: AgentRun) -> None:
        try:
            with self._lock, self._connect() as connection:
                connection.execute(
                    """INSERT INTO runs(
                           id, conversation_id, status, skill_id, answer, error_code, steps,
                           created_at, started_at, completed_at, metadata_json
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    _run_values(run),
                )
                connection.execute(
                    "INSERT INTO run_event_counters(run_id, last_sequence) VALUES (?, 0)",
                    (run.id,),
                )
        except sqlite3.IntegrityError as exc:
            if "runs.conversation_id" in str(exc):
                raise ActiveRunConflictError(
                    f"Conversation already has an active Run: {run.conversation_id}"
                ) from exc
            raise

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

    async def append_event(self, event: AgentEvent) -> AgentEvent:
        persisted = await asyncio.to_thread(self._append_event, event)
        await self._notify(event.run_id)
        return persisted

    def _append_event(self, event: AgentEvent) -> AgentEvent:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """UPDATE run_event_counters SET last_sequence = last_sequence + 1
                   WHERE run_id = ? RETURNING last_sequence""",
                (event.run_id,),
            ).fetchone()
            if row is None:
                raise ValueError(f"Run event counter does not exist: {event.run_id}")
            sequence = int(row[0])
            connection.execute(
                "INSERT INTO run_events(run_id, sequence, type, data_json) VALUES (?, ?, ?, ?)",
                (event.run_id, sequence, event.type,
                 json.dumps(dict(event.data), ensure_ascii=False)),
            )
        return AgentEvent(event.run_id, sequence, event.type, event.data)

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

    async def list_recoverable_runs(self) -> Sequence[AgentRun]:
        return await asyncio.to_thread(self._list_recoverable_runs)

    def _list_recoverable_runs(self) -> Sequence[AgentRun]:
        with self._lock, self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM runs WHERE status = 'queued' ORDER BY created_at, id"
            ).fetchall()
        return tuple(_run(row) for row in rows)

    async def get_trigger_message(self, run_id: str) -> ConversationMessage | None:
        return await asyncio.to_thread(self._get_trigger_message, run_id)

    def _get_trigger_message(self, run_id: str) -> ConversationMessage | None:
        with self._lock, self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM messages
                   WHERE run_id = ? AND role = 'user'
                   ORDER BY created_at, id LIMIT 1""",
                (run_id,),
            ).fetchone()
        return _message(row) if row else None

    async def create_task_graph(
        self, graph: TaskGraph, root_node: TaskNode, root_gate: TaskGate
    ) -> None:
        await asyncio.to_thread(self._create_task_graph, graph, root_node, root_gate)

    def _create_task_graph(
        self, graph: TaskGraph, root_node: TaskNode, root_gate: TaskGate
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO task_graphs(
                       id, run_id, conversation_id, user_intent, status, version,
                       created_at, updated_at, completed_at, metadata_json,
                       parent_graph_id, relation_type, archived_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _graph_values(graph),
            )
            connection.execute(
                """INSERT INTO task_nodes(
                       id, graph_id, parent_node_id, kind, owner_agent_id, title, status,
                       dependencies_json, contract_json, result_summary, error_code,
                       result_json, created_at, started_at, completed_at, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _node_values(root_node),
            )
            connection.execute(
                """INSERT INTO task_gates(
                       id, graph_id, node_id, kind, status, required, evaluator_agent_id,
                       verdict, evidence_json, created_at, evaluated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _gate_values(root_gate),
            )

    async def get_task_graph(self, run_id: str) -> TaskGraphSnapshot | None:
        return await asyncio.to_thread(self._get_task_graph, run_id)

    def _get_task_graph(self, run_id: str) -> TaskGraphSnapshot | None:
        with self._lock, self._connect() as connection:
            graph_row = connection.execute(
                "SELECT * FROM task_graphs WHERE run_id = ?", (run_id,)
            ).fetchone()
            if graph_row is None:
                return None
            graph_id = str(graph_row["id"])
            node_rows = connection.execute(
                "SELECT * FROM task_nodes WHERE graph_id = ? ORDER BY created_at, id",
                (graph_id,),
            ).fetchall()
            gate_rows = connection.execute(
                "SELECT * FROM task_gates WHERE graph_id = ? ORDER BY created_at, id",
                (graph_id,),
            ).fetchall()
            attempt_rows = connection.execute(
                """SELECT * FROM node_attempts WHERE graph_id = ?
                   ORDER BY started_at, attempt_number, id""",
                (graph_id,),
            ).fetchall()
        return TaskGraphSnapshot(
            _graph(graph_row), tuple(_node(row) for row in node_rows),
            tuple(_gate(row) for row in gate_rows),
            tuple(_attempt(row) for row in attempt_rows),
        )

    async def update_task_graph(self, graph: TaskGraph) -> None:
        await asyncio.to_thread(self._update_task_graph, graph)

    def _update_task_graph(self, graph: TaskGraph) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE task_graphs SET status = ?, version = ?, updated_at = ?,
                       completed_at = ?, metadata_json = ?, parent_graph_id = ?,
                       relation_type = ?, archived_at = ? WHERE id = ?""",
                (graph.status.value, graph.version, _time(graph.updated_at),
                 _time(graph.completed_at), json.dumps(dict(graph.metadata), ensure_ascii=False),
                 graph.parent_graph_id, graph.relation_type, _time(graph.archived_at),
                 graph.id),
            )

    async def compare_and_swap_task_graph(
        self, graph: TaskGraph, expected_version: int
    ) -> bool:
        return await asyncio.to_thread(
            self._compare_and_swap_task_graph, graph, expected_version
        )

    def _compare_and_swap_task_graph(
        self, graph: TaskGraph, expected_version: int
    ) -> bool:
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                """UPDATE task_graphs SET status = ?, version = ?, updated_at = ?,
                       completed_at = ?, metadata_json = ?, parent_graph_id = ?,
                       relation_type = ?, archived_at = ?
                   WHERE id = ? AND version = ?""",
                (graph.status.value, graph.version, _time(graph.updated_at),
                 _time(graph.completed_at), json.dumps(dict(graph.metadata), ensure_ascii=False),
                 graph.parent_graph_id, graph.relation_type, _time(graph.archived_at),
                 graph.id, expected_version),
            )
            return cursor.rowcount == 1

    async def upsert_task_node(self, node: TaskNode) -> None:
        await asyncio.to_thread(self._upsert_task_node, node)

    def _upsert_task_node(self, node: TaskNode) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO task_nodes(
                       id, graph_id, parent_node_id, kind, owner_agent_id, title, status,
                       dependencies_json, contract_json, result_summary, error_code,
                       result_json, created_at, started_at, completed_at, metadata_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status, result_summary = excluded.result_summary,
                       result_json = excluded.result_json,
                       error_code = excluded.error_code, started_at = excluded.started_at,
                       completed_at = excluded.completed_at, metadata_json = excluded.metadata_json""",
                _node_values(node),
            )

    async def upsert_task_gate(self, gate: TaskGate) -> None:
        await asyncio.to_thread(self._upsert_task_gate, gate)

    def _upsert_task_gate(self, gate: TaskGate) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO task_gates(
                       id, graph_id, node_id, kind, status, required, evaluator_agent_id,
                       verdict, evidence_json, created_at, evaluated_at
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(id) DO UPDATE SET
                       status = excluded.status, evaluator_agent_id = excluded.evaluator_agent_id,
                       verdict = excluded.verdict, evidence_json = excluded.evidence_json,
                       evaluated_at = excluded.evaluated_at""",
                _gate_values(gate),
            )

    async def create_node_attempt(self, attempt: NodeAttempt) -> None:
        await asyncio.to_thread(self._create_node_attempt, attempt)

    def _create_node_attempt(self, attempt: NodeAttempt) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """INSERT INTO node_attempts(
                       id, graph_id, node_id, attempt_number, status, lease_owner,
                       lease_expires_at, started_at, completed_at, error_code, checkpoint_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _attempt_values(attempt),
            )

    async def update_node_attempt(self, attempt: NodeAttempt) -> None:
        await asyncio.to_thread(self._update_node_attempt, attempt)

    def _update_node_attempt(self, attempt: NodeAttempt) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """UPDATE node_attempts SET status = ?, lease_owner = ?, completed_at = ?,
                       lease_expires_at = ?, error_code = ?, checkpoint_json = ? WHERE id = ?""",
                (attempt.status.value, attempt.lease_owner, _time(attempt.completed_at),
                 _time(attempt.lease_expires_at),
                 attempt.error_code, json.dumps(dict(attempt.checkpoint), ensure_ascii=False),
                 attempt.id),
            )

    async def claim_task_node(
        self, node_id: str, lease_owner: str, lease_seconds: int
    ) -> NodeAttempt | None:
        return await asyncio.to_thread(
            self._claim_task_node, node_id, lease_owner, lease_seconds
        )

    def _claim_task_node(
        self, node_id: str, lease_owner: str, lease_seconds: int
    ) -> NodeAttempt | None:
        now = datetime.now(timezone.utc)
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM task_nodes WHERE id = ?", (node_id,)
            ).fetchone()
            if row is None or str(row["status"]) != "pending":
                return None
            dependencies = tuple(json.loads(row["dependencies_json"]))
            if dependencies:
                placeholders = ",".join("?" for _ in dependencies)
                dependency_rows = connection.execute(
                    f"""SELECT n.id, n.status,
                               COALESCE(MIN(CASE WHEN g.required = 1 THEN
                                   CASE WHEN g.status = 'passed' THEN 1 ELSE 0 END
                               ELSE 1 END), 1) AS gates_passed
                        FROM task_nodes n LEFT JOIN task_gates g ON g.node_id = n.id
                        WHERE n.id IN ({placeholders}) GROUP BY n.id, n.status""",
                    dependencies,
                ).fetchall()
                if len(dependency_rows) != len(dependencies) or any(
                    str(item["status"]) != "completed" or int(item["gates_passed"]) != 1
                    for item in dependency_rows
                ):
                    return None
            attempt_number = int(connection.execute(
                "SELECT COALESCE(MAX(attempt_number), 0) + 1 FROM node_attempts WHERE node_id = ?",
                (node_id,),
            ).fetchone()[0])
            expires = now + timedelta(seconds=lease_seconds)
            cursor = connection.execute(
                """UPDATE task_nodes SET status = 'running', started_at = ?,
                       completed_at = NULL, error_code = NULL
                   WHERE id = ? AND status = 'pending'""",
                (now.isoformat(), node_id),
            )
            if cursor.rowcount != 1:
                return None
            attempt = NodeAttempt(
                id=str(uuid4()), graph_id=str(row["graph_id"]), node_id=node_id,
                attempt_number=attempt_number, lease_owner=lease_owner,
                lease_expires_at=expires, started_at=now,
            )
            connection.execute(
                """INSERT INTO node_attempts(
                       id, graph_id, node_id, attempt_number, status, lease_owner,
                       lease_expires_at, started_at, completed_at, error_code, checkpoint_json
                   ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                _attempt_values(attempt),
            )
            return attempt

    async def commit_task_node(
        self, node: TaskNode, gate: TaskGate, attempt: NodeAttempt
    ) -> None:
        await asyncio.to_thread(self._commit_task_node, node, gate, attempt)

    def _commit_task_node(
        self, node: TaskNode, gate: TaskGate, attempt: NodeAttempt
    ) -> None:
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE task_nodes SET status = ?, result_summary = ?, result_json = ?,
                       error_code = ?, started_at = ?, completed_at = ?, metadata_json = ?
                   WHERE id = ?""",
                (node.status.value, node.result_summary,
                 json.dumps(dict(node.result), ensure_ascii=False), node.error_code,
                 _time(node.started_at), _time(node.completed_at),
                 json.dumps(dict(node.metadata), ensure_ascii=False), node.id),
            )
            connection.execute(
                """UPDATE task_gates SET status = ?, evaluator_agent_id = ?, verdict = ?,
                       evidence_json = ?, evaluated_at = ? WHERE id = ?""",
                (gate.status.value, gate.evaluator_agent_id, gate.verdict,
                 json.dumps(list(gate.evidence), ensure_ascii=False),
                 _time(gate.evaluated_at), gate.id),
            )
            connection.execute(
                """UPDATE node_attempts SET status = ?, lease_owner = NULL,
                       lease_expires_at = NULL, completed_at = ?, error_code = ?,
                       checkpoint_json = ? WHERE id = ? AND status = 'running'""",
                (attempt.status.value, _time(attempt.completed_at), attempt.error_code,
                 json.dumps(dict(attempt.checkpoint), ensure_ascii=False), attempt.id),
            )

    async def close_open_task_nodes(
        self, run_id: str, cancelled: bool, error_code: str
    ) -> None:
        await asyncio.to_thread(
            self._close_open_task_nodes, run_id, cancelled, error_code
        )

    def _close_open_task_nodes(
        self, run_id: str, cancelled: bool, error_code: str
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        node_status = "cancelled" if cancelled else "skipped"
        attempt_status = "cancelled" if cancelled else "failed"
        with self._lock, self._connect() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """UPDATE task_nodes SET status = ?, completed_at = ?, error_code = ?
                   WHERE graph_id = (SELECT id FROM task_graphs WHERE run_id = ?)
                     AND kind <> 'orchestration' AND status IN ('pending', 'running')""",
                (node_status, now, error_code, run_id),
            )
            connection.execute(
                """UPDATE task_gates SET status = 'blocked', verdict = ?, evaluated_at = ?
                   WHERE graph_id = (SELECT id FROM task_graphs WHERE run_id = ?)
                     AND status = 'pending'""",
                (error_code, now, run_id),
            )
            connection.execute(
                """UPDATE node_attempts SET status = ?, completed_at = ?, error_code = ?,
                       lease_owner = NULL, lease_expires_at = NULL
                   WHERE graph_id = (SELECT id FROM task_graphs WHERE run_id = ?)
                     AND status = 'running'""",
                (attempt_status, now, error_code, run_id),
            )

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
        str(row["algorithm_version"]), str(row["token_counter"]),
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


def _graph_values(graph: TaskGraph) -> tuple[Any, ...]:
    return (
        graph.id, graph.run_id, graph.conversation_id, graph.user_intent,
        graph.status.value, graph.version, _time(graph.created_at), _time(graph.updated_at),
        _time(graph.completed_at), json.dumps(dict(graph.metadata), ensure_ascii=False),
        graph.parent_graph_id, graph.relation_type, _time(graph.archived_at),
    )


def _node_values(node: TaskNode) -> tuple[Any, ...]:
    return (
        node.id, node.graph_id, node.parent_node_id, node.kind, node.owner_agent_id,
        node.title, node.status.value, json.dumps(list(node.dependencies), ensure_ascii=False),
        json.dumps(dict(node.contract.to_data()), ensure_ascii=False), node.result_summary,
        node.error_code, json.dumps(dict(node.result), ensure_ascii=False),
        _time(node.created_at), _time(node.started_at),
        _time(node.completed_at), json.dumps(dict(node.metadata), ensure_ascii=False),
    )


def _gate_values(gate: TaskGate) -> tuple[Any, ...]:
    return (
        gate.id, gate.graph_id, gate.node_id, gate.kind, gate.status.value,
        int(gate.required), gate.evaluator_agent_id, gate.verdict,
        json.dumps(list(gate.evidence), ensure_ascii=False), _time(gate.created_at),
        _time(gate.evaluated_at),
    )


def _attempt_values(attempt: NodeAttempt) -> tuple[Any, ...]:
    return (
        attempt.id, attempt.graph_id, attempt.node_id, attempt.attempt_number,
        attempt.status.value, attempt.lease_owner, _time(attempt.lease_expires_at),
        _time(attempt.started_at),
        _time(attempt.completed_at), attempt.error_code,
        json.dumps(dict(attempt.checkpoint), ensure_ascii=False),
    )


def _graph(row: sqlite3.Row) -> TaskGraph:
    return TaskGraph(
        id=str(row["id"]), run_id=str(row["run_id"]),
        conversation_id=str(row["conversation_id"]), user_intent=str(row["user_intent"]),
        status=TaskGraphStatus(str(row["status"])), version=int(row["version"]),
        created_at=_parse_time(row["created_at"]), updated_at=_parse_time(row["updated_at"]),
        completed_at=_parse_time(row["completed_at"]), metadata=json.loads(row["metadata_json"]),
        parent_graph_id=row["parent_graph_id"], relation_type=row["relation_type"],
        archived_at=_parse_time(row["archived_at"]),
    )  # type: ignore[arg-type]


def _node(row: sqlite3.Row) -> TaskNode:
    return TaskNode(
        id=str(row["id"]), graph_id=str(row["graph_id"]), kind=str(row["kind"]),
        owner_agent_id=str(row["owner_agent_id"]), title=str(row["title"]),
        contract=AcceptanceContract.from_data(json.loads(row["contract_json"])),
        status=TaskNodeStatus(str(row["status"])), parent_node_id=row["parent_node_id"],
        dependencies=tuple(json.loads(row["dependencies_json"])),
        result_summary=str(row["result_summary"]), error_code=row["error_code"],
        result=json.loads(row["result_json"]),
        created_at=_parse_time(row["created_at"]), started_at=_parse_time(row["started_at"]),
        completed_at=_parse_time(row["completed_at"]), metadata=json.loads(row["metadata_json"]),
    )  # type: ignore[arg-type]


def _gate(row: sqlite3.Row) -> TaskGate:
    return TaskGate(
        id=str(row["id"]), graph_id=str(row["graph_id"]), node_id=str(row["node_id"]),
        kind=str(row["kind"]), status=GateStatus(str(row["status"])),
        required=bool(row["required"]), evaluator_agent_id=row["evaluator_agent_id"],
        verdict=str(row["verdict"]), evidence=tuple(json.loads(row["evidence_json"])),
        created_at=_parse_time(row["created_at"]), evaluated_at=_parse_time(row["evaluated_at"]),
    )  # type: ignore[arg-type]


def _attempt(row: sqlite3.Row) -> NodeAttempt:
    return NodeAttempt(
        id=str(row["id"]), graph_id=str(row["graph_id"]), node_id=str(row["node_id"]),
        attempt_number=int(row["attempt_number"]), status=AttemptStatus(str(row["status"])),
        lease_owner=row["lease_owner"], lease_expires_at=_parse_time(row["lease_expires_at"]),
        started_at=_parse_time(row["started_at"]),
        completed_at=_parse_time(row["completed_at"]), error_code=row["error_code"],
        checkpoint=json.loads(row["checkpoint_json"]),
    )  # type: ignore[arg-type]
