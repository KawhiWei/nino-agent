from __future__ import annotations

import asyncio
import tempfile
import time
import unittest
from pathlib import Path
from typing import Sequence

from fastapi.testclient import TestClient

from api.app import create_app
from demo import DemoToolClient
from infrastructure import SqliteAgentRepository
from framework import Message, ModelTurn, ToolDefinition
from harness import ReActHarness, SkillRegistry


def wait_for_terminal(client: TestClient, run_id: str) -> dict:
    for _ in range(100):
        response = client.get(f"/api/v1/runs/{run_id}")
        response.raise_for_status()
        run = response.json()
        if run["status"] in {"completed", "failed", "cancelled"}:
            return run
        time.sleep(0.01)
    raise AssertionError(f"Run did not complete: {run_id}")


class SlowModel:
    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        await asyncio.sleep(60)
        return ModelTurn(text="Should have been cancelled")


class AgentApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self._storage = tempfile.TemporaryDirectory()

    def tearDown(self) -> None:
        self._storage.cleanup()

    def app(self, **kwargs):
        repository = SqliteAgentRepository(Path(self._storage.name) / "agent.db")
        return create_app(repository=repository, **kwargs)

    def test_health_skills_and_conversation_run_lifecycle(self) -> None:
        with TestClient(self.app()) as client:
            health = client.get("/health")
            self.assertEqual(200, health.status_code)
            self.assertEqual("demo", health.json()["runtime_mode"])

            skills = client.get("/api/v1/skills")
            self.assertEqual(200, skills.status_code)
            self.assertEqual("nino-data.analysis", skills.json()[0]["id"])
            self.assertEqual(4, len(skills.json()[0]["references"]))
            self.assertTrue(skills.json()[0]["semantic_routing"])
            self.assertEqual("business-analysis", skills.json()[0]["workflow_id"])
            self.assertEqual("strict_verify", skills.json()[0]["assurance_mode"])

            agents = client.get("/api/v1/agents")
            self.assertEqual(200, agents.status_code)
            self.assertEqual(3, len(agents.json()))
            self.assertEqual(
                "nino.orchestrator",
                next(item for item in agents.json() if item["mode"] == "primary")["id"],
            )
            self.assertEqual([], client.get("/api/v1/mcp/servers").json())

            created = client.post("/api/v1/conversations", json={"title": "July analysis"})
            self.assertEqual(201, created.status_code)
            conversation_id = created.json()["id"]
            listed = client.get("/api/v1/conversations")
            self.assertEqual([conversation_id], [item["id"] for item in listed.json()])

            accepted = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "查询订单 DEMO-202607-001 的毛利"},
            )
            self.assertEqual(202, accepted.status_code, accepted.text)
            self.assertEqual("queued", accepted.json()["status"])
            run_id = accepted.json()["run_id"]

            run = wait_for_terminal(client, run_id)
            self.assertEqual("completed", run["status"])
            self.assertEqual("nino-data.analysis", run["skill_id"])
            self.assertIn("demo_gross_margin", run["answer"])
            graph = client.get(f"/api/v1/runs/{run_id}/task-graph").json()
            self.assertEqual("completed", graph["graph"]["status"])
            self.assertEqual(
                {"orchestration", "specialist", "verification"},
                {node["kind"] for node in graph["nodes"]},
            )
            self.assertEqual(
                "graph_planned", graph["graph"]["metadata"]["revisions"][0]["event"]
            )
            specialist = next(node for node in graph["nodes"] if node["kind"] == "specialist")
            self.assertIn("查询订单", specialist["contract"]["target_outcome"])
            self.assertIn("outputs", specialist["result"])
            verification_gate = next(
                gate for gate in graph["gates"]
                if gate["kind"] == "independent_verification"
            )
            self.assertEqual("passed", verification_gate["status"])
            runs = client.get(
                f"/api/v1/conversations/{conversation_id}/runs"
            ).json()
            self.assertEqual([run_id], [item["id"] for item in runs])

            messages = client.get(
                f"/api/v1/conversations/{conversation_id}/messages"
            ).json()
            self.assertEqual(["user", "assistant"], [item["role"] for item in messages])

            events = client.get(f"/api/v1/runs/{run_id}/events").json()
            sequences = [event["sequence"] for event in events["events"]]
            self.assertEqual(sorted(sequences), sequences)
            self.assertEqual(len(sequences), len(set(sequences)))
            self.assertIn("run_completed", [event["type"] for event in events["events"]])

            orchestration_loop = client.get(
                f"/api/v1/runs/{run_id}/loop-checkpoint",
                params={"kind": "orchestration"},
            ).json()
            self.assertEqual("completed", orchestration_loop["data"]["state"]["status"])
            self.assertEqual(
                "final_answer", orchestration_loop["data"]["state"]["stop_reason"]
            )
            worker_loop = client.get(
                f"/api/v1/runs/{run_id}/loop-checkpoint",
                params={"kind": "worker_react"},
            ).json()
            self.assertEqual("completed", worker_loop["data"]["state"]["status"])

            resumed = client.get(
                f"/api/v1/runs/{run_id}/events", params={"after": sequences[-2]}
            ).json()
            self.assertEqual([sequences[-1]], [event["sequence"] for event in resumed["events"]])

    def test_sse_stream_returns_ordered_events_and_finishes(self) -> None:
        with TestClient(self.app()) as client:
            conversation_id = client.post("/api/v1/conversations", json={}).json()["id"]
            run_id = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "统计 2026 年 7 月各业务线毛利"},
            ).json()["run_id"]
            wait_for_terminal(client, run_id)

            with client.stream("GET", f"/api/v1/runs/{run_id}/events/stream") as response:
                self.assertEqual(200, response.status_code)
                body = "\n".join(response.iter_lines())
            self.assertIn("event: run_started", body)
            self.assertIn("event: tool_completed", body)
            self.assertIn("event: run_completed", body)

    def test_not_found_uses_stable_error_envelope(self) -> None:
        with TestClient(self.app()) as client:
            response = client.get("/api/v1/runs/missing")
            self.assertEqual(404, response.status_code)
            self.assertEqual("RESOURCE_NOT_FOUND", response.json()["error"]["code"])

    def test_blank_message_uses_invalid_argument_envelope(self) -> None:
        with TestClient(self.app()) as client:
            conversation_id = client.post("/api/v1/conversations", json={}).json()["id"]
            response = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "   "},
            )
            self.assertEqual(400, response.status_code)
            self.assertEqual("INVALID_ARGUMENT", response.json()["error"]["code"])

    def test_can_cancel_active_run(self) -> None:
        skills = SkillRegistry.load(
            Path(__file__).resolve().parents[2] / "shared" / "skills"
        )
        harness = ReActHarness(SlowModel(), DemoToolClient(), skills)
        with TestClient(self.app(harness=harness, skills=skills, runtime_mode="test")) as client:
            conversation_id = client.post("/api/v1/conversations", json={}).json()["id"]
            run_id = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "查询订单 DEMO-202607-001"},
            ).json()["run_id"]

            duplicate = client.post(
                f"/api/v1/conversations/{conversation_id}/messages",
                json={"content": "同一个会话的并发请求"},
            )
            self.assertEqual(409, duplicate.status_code)
            self.assertEqual("RUN_CONFLICT", duplicate.json()["error"]["code"])

            response = client.post(f"/api/v1/runs/{run_id}/cancel")
            self.assertEqual(200, response.status_code, response.text)
            self.assertEqual("cancelled", response.json()["status"])
            events = client.get(f"/api/v1/runs/{run_id}/events").json()["events"]
            self.assertIn("run_cancelled", [event["type"] for event in events])


if __name__ == "__main__":
    unittest.main()
