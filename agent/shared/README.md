# Shared Agent Contracts

`agent/shared` is the language-neutral source of truth used by every Agent implementation.

```text
shared/
├── contracts/     # JSON Schema for machine validation
├── skills/        # Discoverable task capabilities, tool allowlists, and on-demand references
└── agents/        # Business-neutral primary, specialist capabilities, and dispatch policy
```

Rules:

1. Shared files cannot import or reference Python, Node.js, or .NET implementation code.
2. `skill.json.id` and `agent.json.id` are stable cross-language identities.
3. `SKILL.md` and `AGENT.md` frontmatter own display `name` and `description`.
4. Reference paths are relative to their Skill directory and must be containment-checked.
5. Every language Runtime must validate the JSON contracts before exposing a Skill or Agent.
6. Tool names refer to MCP capabilities; shared Skills never contain SQL, credentials, or transport URLs.
7. Language implementations may optimize Harness/Runtime internals but must preserve shared IDs,
   allowlist semantics, delegation depth, and externally documented events.
8. The primary Orchestrator reads capability metadata only. A selected specialist loads the full
   Skill instructions, References, and MCP schemas in a fresh task context.
9. `discover_delegates` dynamically includes registered specialists, but never bypasses each
   specialist's `allowed_skills` and `allowed_tools` enforcement.
10. Agent and Skill `loop` values are policy ceilings. A Runtime combines every field with its hard
    limit using `min`; business definitions may tighten but never widen execution budgets.

Python loads this directory through `NINO_SKILLS_PATH` and `NINO_AGENTS_PATH`. Future Node.js and
.NET implementations must load the same directory rather than copying its contents into their own
projects.
