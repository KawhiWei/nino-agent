# Nino Agent Local Storage

The Python Agent Runtime stores local SQLite state in `nino-agent.db` by default.

The database contains conversations, messages, runs, replayable events, and compacted
conversation context. SQLite database, WAL, and shared-memory files are intentionally ignored by
Git. Back up or remove this directory only when the Agent Runtime is stopped.
