# Standard Question Banks

`question-banks/` contains fixed, versioned evaluation suites shared by every Agent Runtime
implementation. Tests load these files; they never ask a model to generate replacement questions.

## Rules

1. Keep one directory per business capability, for example `nino-data-analysis/`.
2. Keep stable case IDs so reports remain comparable across commits and languages.
3. Every case must record `derived_from` and deterministic expected evidence.
4. Update expected facts only when the Skill, Tool contract, metric definition, or seed truth changes.
5. Increase the suite version whenever a case, expectation, or derivation changes.
6. Keep the standard suite small. Use tags such as `smoke` and `standard` to control execution cost.
7. Never place generated benchmark reports in this directory.

## Nino Data

The canonical suite is `nino-data-analysis/standard.json`. It contains eight representative cases;
the five `smoke` cases are the default low-cost regression set. Database rows are aggregated by MCP
Tools and are not copied into prompts.

```bash
cd agent/python
.venv/bin/python evals/live_benchmark.py --list
.venv/bin/python evals/live_benchmark.py --tag smoke
```

Use `--tag standard` only for a release or a material Harness, Skill, Tool, or model change.
