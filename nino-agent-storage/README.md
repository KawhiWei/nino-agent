# Nino Agent Local Storage

The Python Agent Runtime stores local SQLite state in `nino-agent.db` by default.

The database contains conversations, messages, runs, replayable events, and compacted
conversation context. SQLite database, WAL, and shared-memory files are intentionally ignored by
Git. Back up or remove this directory only when the Agent Runtime is stopped.

Committed `live-benchmark*.json` files are historical benchmark artifacts. Their recorded Tool names
and Agent IDs describe the Runtime version that produced them and must not be rewritten to imitate a
new architecture. Generate a new report with Runtime `0.14.0` when comparison against the Planner /
generic Analyst / generic Verifier flow is required.
