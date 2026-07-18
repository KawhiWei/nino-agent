# Shared Agent Contracts

`agent/shared` is the language-neutral source of truth used by every Agent implementation.

```text
shared/
├── contracts/     # JSON Schema for machine validation
├── skills/        # Capabilities, tool allowlists, references, and standard evaluation suites
└── agents/        # Business-neutral Orchestrator, Planner, Analyst, Verifier, and role policies
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
8. The Planner reads capability metadata and proposes candidate Graph nodes only. The Orchestrator
   alone validates, persists, schedules, reconciles, and completes accepted work.
9. Generic Analyst and Verifier Agents load the selected Skill in a fresh context. Effective Tool
   access is the intersection of discovered MCP tools, Skill allowlists, and Agent role policy.
10. Agent and Skill `loop` values are policy ceilings. A Runtime combines every field with its hard
    limit using `min`; business definitions may tighten but never widen execution budgets.
11. `excluded_intent_keywords` are evaluated before positive `intent_keywords`. An unmatched request
    must not fall back to a default Skill or reach the model.
12. Each production Skill should declare versioned `evaluation_suites` stored under
    `question-banks/<capability>/`. Every case records
    `derived_from` provenance and may only reference Tools and References owned by that Skill.
13. The standard business-neutral Agent set is `nino.orchestrator`, `nino.planner`, `nino.analyst`,
    and `nino.verifier`. Compatible new read-only businesses normally add Skills and MCP servers,
    not Agent manifests.

Python loads this directory through `NINO_SKILLS_PATH` and `NINO_AGENTS_PATH`. Future Node.js and
.NET implementations must load the same directory rather than copying its contents into their own
projects.

## Standard Agent Contract

| ID | Role | Shared policy |
|---|---|---|
| `nino.orchestrator` | sole control plane | No business Skill/Tool binding; owns accepted Graph execution and final reconciliation |
| `nino.planner` | advisory planner | No business Skill/Tool binding; proposes candidate nodes only |
| `nino.analyst` | generic worker | Accepts compatible `read-only` Skills; Tools come from the selected Skill policy |
| `nino.verifier` | generic verification evaluator | Independently re-runs compatible `read-only` Skill evidence |

An empty `allowed_skills` on the generic Analyst/Verifier does not mean unrestricted access and does
not mean no capability. With `tool_policy=selected-skill-only`, the Runtime first checks
`accepted_risk_levels/accepted_capabilities`, then exposes only tools present in both MCP discovery and
the selected `Skill.allowed_tools`. Explicit allowlists remain available for future narrowly bound
roles.
