from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
INTERNAL_TOOLS = {
    "nino_runtime_submit_task_graph_node",
    "nino_runtime_load_reference",
    "nino_runtime_request_clarification",
}
DEFAULT_SUITE = (
    Path(__file__).resolve().parents[2]
    / "shared/question-banks/nino-data-analysis/standard.json"
)


@dataclass(frozen=True, slots=True)
class BenchmarkCase:
    id: str
    category: str
    tags: tuple[str, ...]
    prompt: str
    derived_from: tuple[str, ...]
    expected_status: str
    expected_skill: str | None
    expect_dispatch: bool
    required_tools: tuple[str, ...]
    forbidden_tools: tuple[str, ...]
    required_references: tuple[str, ...]
    answer_facts: tuple[tuple[str, ...], ...]
    max_model_calls: int | None


@dataclass(frozen=True, slots=True)
class EvaluationSuite:
    id: str
    version: str
    skill_id: str
    description: str
    derived_from: tuple[str, ...]
    path: Path
    cases: tuple[BenchmarkCase, ...]


def load_suite(path: Path) -> EvaluationSuite:
    """Load a shared Skill suite and reject contract drift before model calls."""

    resolved = path.resolve()
    raw = json.loads(resolved.read_text(encoding="utf-8"))
    shared_dir = next(
        (parent for parent in resolved.parents if (parent / "skills").is_dir()),
        None,
    )
    if shared_dir is None:
        raise ValueError("Evaluation suite must be located under the shared contract directory.")
    manifests = []
    for manifest_path in (shared_dir / "skills").glob("*/skill.json"):
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("id") == raw.get("skill_id"):
            manifests.append((manifest_path.parent, manifest))
    if len(manifests) != 1:
        raise ValueError(
            f"Evaluation suite skill_id must match exactly one Skill manifest: "
            f"{raw.get('skill_id')!r}."
        )
    skill_dir, manifest = manifests[0]
    declared = {
        (skill_dir / relative).resolve()
        for relative in manifest.get("evaluation_suites", ())
    }
    if resolved not in declared:
        raise ValueError("Evaluation suite is not declared by skill.json.")

    allowed_tools = set(manifest["allowed_tools"]) | INTERNAL_TOOLS
    allowed_references = {item["id"] for item in manifest.get("references", ())}
    cases: list[BenchmarkCase] = []
    for item in raw.get("cases", ()):
        expected = item["expected"]
        required_tools = tuple(expected.get("required_tools", ()))
        forbidden_tools = tuple(expected.get("forbidden_tools", ()))
        required_references = tuple(expected.get("required_references", ()))
        unknown_tools = (set(required_tools) | set(forbidden_tools)) - allowed_tools
        if unknown_tools:
            raise ValueError(
                f"Case {item['id']} references tools outside the Skill contract: "
                f"{', '.join(sorted(unknown_tools))}"
            )
        unknown_references = set(required_references) - allowed_references
        if unknown_references:
            raise ValueError(
                f"Case {item['id']} references unknown Skill references: "
                f"{', '.join(sorted(unknown_references))}"
            )
        expected_skill = expected.get("skill_id")
        if expected_skill not in {None, raw["skill_id"]}:
            raise ValueError(f"Case {item['id']} expects a different Skill.")
        cases.append(BenchmarkCase(
            id=item["id"],
            category=item["category"],
            tags=tuple(item["tags"]),
            prompt=item["prompt"],
            derived_from=tuple(item["derived_from"]),
            expected_status=expected["status"],
            expected_skill=expected_skill,
            expect_dispatch=bool(expected["dispatch"]),
            required_tools=required_tools,
            forbidden_tools=forbidden_tools,
            required_references=required_references,
            answer_facts=tuple(tuple(group) for group in expected["answer_facts"]),
            max_model_calls=expected.get("max_model_calls"),
        ))
    if not cases or len({case.id for case in cases}) != len(cases):
        raise ValueError("Evaluation suite must contain unique case ids.")
    if not raw.get("derived_from") or any(not case.derived_from for case in cases):
        raise ValueError("Suite and every case require derivation provenance.")
    return EvaluationSuite(
        id=raw["id"],
        version=raw["version"],
        skill_id=raw["skill_id"],
        description=raw["description"],
        derived_from=tuple(raw["derived_from"]),
        path=resolved,
        cases=tuple(cases),
    )


class RuntimeClient:
    def __init__(self, base_url: str, timeout_seconds: float) -> None:
        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds

    def request(self, method: str, path: str, body: dict[str, Any] | None = None) -> Any:
        data = json.dumps(body, ensure_ascii=False).encode() if body is not None else None
        request = urllib.request.Request(
            f"{self._base_url}{path}", data=data, method=method,
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self._timeout_seconds) as response:
                return json.loads(response.read())
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode(errors="replace")
            raise RuntimeError(f"Runtime HTTP {exc.code}: {detail}") from exc

    def run(self, case: BenchmarkCase) -> tuple[dict[str, Any], list[dict[str, Any]]]:
        conversation = self.request(
            "POST", "/api/v1/conversations", {"title": f"Live benchmark: {case.id}"}
        )
        queued = self.request(
            "POST", f"/api/v1/conversations/{conversation['id']}/messages",
            {"content": case.prompt},
        )
        run_id = queued["run_id"]
        deadline = time.monotonic() + self._timeout_seconds
        while time.monotonic() < deadline:
            run = self.request("GET", f"/api/v1/runs/{run_id}")
            if run["status"] in TERMINAL_STATUSES:
                events = self.request("GET", f"/api/v1/runs/{run_id}/events")["events"]
                return run, events
            time.sleep(0.5)
        raise TimeoutError(f"Run {run_id} did not finish in {self._timeout_seconds:.0f}s")


def score(case: BenchmarkCase, run: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    answer = run.get("answer", "")
    answer_lower = answer.lower()
    tool_names = {
        event["data"].get("tool")
        for event in events if event["type"] == "tool_started"
    }
    references = {
        event["data"].get("reference_id")
        for event in events if event["type"] == "reference_loaded"
    }
    checkpoints = [
        event["data"]["state"]
        for event in events
        if event["type"] == "loop_checkpoint" and "state" in event["data"]
    ]
    model_calls = sum(event["type"] == "model_started" for event in events)

    completed_score = 20 if run["status"] == case.expected_status else 0
    dispatched = "nino_runtime_submit_task_graph_node" in tool_names
    route_ok = dispatched == case.expect_dispatch and run.get("skill_id") == case.expected_skill
    routing_score = 20 if route_ok else 0

    evidence_checks = [tool in tool_names for tool in case.required_tools]
    evidence_checks.extend(tool not in tool_names for tool in case.forbidden_tools)
    evidence_checks.extend(reference in references for reference in case.required_references)
    evidence_score = round(20 * sum(evidence_checks) / len(evidence_checks)) if evidence_checks else 20

    fact_checks = [
        any(alternative.lower() in answer_lower for alternative in alternatives)
        for alternatives in case.answer_facts
    ]
    facts_score = round(30 * sum(fact_checks) / len(fact_checks)) if fact_checks else 30

    no_tool_errors = not any(
        event["type"] == "tool_completed" and event["data"].get("is_error")
        for event in events
    )
    within_budgets = bool(checkpoints) and all(
        state["step"] <= state["max_steps"]
        and state["action_count"] <= state["max_actions"]
        and state["elapsed_ms"] <= state["timeout_seconds"] * 1000
        for state in checkpoints
    )
    model_call_policy = (
        case.max_model_calls is None or model_calls <= case.max_model_calls
    )
    loop_score = 10 if no_tool_errors and within_budgets and model_call_policy else 0

    return {
        "id": case.id,
        "category": case.category,
        "tags": list(case.tags),
        "derived_from": list(case.derived_from),
        "run_id": run["id"],
        "status": run["status"],
        "score": completed_score + routing_score + evidence_score + facts_score + loop_score,
        "scores": {
            "status": completed_score,
            "routing": routing_score,
            "evidence": evidence_score,
            "answer_facts": facts_score,
            "loop_safety": loop_score,
        },
        "tools": sorted(tool for tool in tool_names if tool),
        "references": sorted(reference for reference in references if reference),
        "model_calls": model_calls,
        "max_observed_step": max((state["step"] for state in checkpoints), default=0),
        "max_observed_actions": max((state["action_count"] for state in checkpoints), default=0),
        "answer": answer,
        "error_code": run.get("error_code"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a shared Skill evaluation suite.")
    parser.add_argument("--suite", type=Path, default=DEFAULT_SUITE)
    parser.add_argument("--base-url", default="http://127.0.0.1:8090")
    parser.add_argument("--timeout", type=float, default=240)
    parser.add_argument("--case", action="append", dest="case_ids")
    parser.add_argument("--tag", action="append", dest="tags")
    parser.add_argument("--list", action="store_true", dest="list_cases")
    parser.add_argument("--output")
    args = parser.parse_args()

    suite = load_suite(args.suite)
    selected = [
        case for case in suite.cases
        if (not args.case_ids or case.id in args.case_ids)
        and (not args.tags or set(args.tags) & set(case.tags))
    ]
    unknown = set(args.case_ids or ()) - {case.id for case in suite.cases}
    if unknown:
        parser.error(f"unknown case(s): {', '.join(sorted(unknown))}")
    if args.list_cases:
        for case in selected:
            print(f"{case.id}\t{case.category}\t{','.join(case.tags)}")
        return
    if not selected:
        parser.error("no cases matched the supplied filters")

    client = RuntimeClient(args.base_url, args.timeout)
    results = []
    for index, case in enumerate(selected, start=1):
        print(f"[{index}/{len(selected)}] {case.id}", flush=True)
        try:
            run, events = client.run(case)
            result = score(case, run, events)
        except Exception as exc:
            result = {
                "id": case.id, "category": case.category,
                "status": "benchmark_error", "score": 0, "error": str(exc),
            }
        results.append(result)
        print(f"  status={result['status']} score={result['score']}", flush=True)

    report = {
        "suite": {
            "id": suite.id, "version": suite.version, "skill_id": suite.skill_id,
            "path": str(suite.path), "derived_from": list(suite.derived_from),
        },
        "runtime": client.request("GET", "/health"),
        "case_count": len(results),
        "score": round(sum(result["score"] for result in results) / len(results), 1),
        "results": results,
    }
    output = json.dumps(report, ensure_ascii=False, indent=2)
    print(output)
    if args.output:
        Path(args.output).write_text(output + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
