from __future__ import annotations

import importlib.util
import unittest
from pathlib import Path

from demo import DemoChatModel, DemoToolClient
from harness import (
    AgentRegistry,
    LangGraphReActHarness,
    OrchestratorHarness,
    ReActHarness,
    ReferenceProvider,
    SkillRegistry,
)


SHARED = Path(__file__).resolve().parents[2] / "shared"


class AgentAndReferenceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.skills = SkillRegistry.load(SHARED / "skills")
        self.agents = AgentRegistry.load(SHARED / "agents")

    def test_loads_frontmatter_agents_and_safe_references(self) -> None:
        skill = self.skills.get("nino-data.analysis")
        primary = self.agents.primary

        self.assertEqual("nino-data-analysis", skill.name)
        self.assertEqual(4, len(skill.references))
        self.assertEqual(("verification",), skill.required_evaluators)
        self.assertTrue(skill.semantic_routing)
        self.assertEqual("business-analysis", skill.workflow_id)
        self.assertEqual("adaptive", skill.workflow_execution_shape)
        self.assertEqual("strict_verify", skill.assurance_mode)
        self.assertEqual("nino.orchestrator", primary.id)
        self.assertEqual("nino.planner", self.agents.planner.id)
        self.assertTrue(primary.discover_delegates)
        self.assertEqual(
            {"nino.analyst", "nino.verifier"},
            {agent.id for agent in self.agents.delegates_for(primary)},
        )
        analyst = self.agents.get("nino.analyst")
        verifier = self.agents.get("nino.verifier")
        self.assertTrue(analyst.accepts_skill(skill))
        self.assertTrue(verifier.accepts_skill(skill))
        self.assertEqual(skill.allowed_tools, analyst.effective_tools(skill))
        self.assertEqual(skill.allowed_tools, verifier.effective_tools(skill))
        self.assertEqual(frozenset(), self.agents.planner.allowed_tools)
        loaded = ReferenceProvider().load(skill, "metric-definitions")
        self.assertIn("Demo gross margin", loaded.content)
        self.assertEqual(64, len(loaded.sha256))

    async def test_lightweight_loads_reference_for_simple_analysis(self) -> None:
        model = DemoChatModel()
        tools = DemoToolClient()
        references = ReferenceProvider()
        harness = OrchestratorHarness(
            model, self.skills, self.agents,
            lambda agent: ReActHarness(
                model, tools, self.skills, references=references,
                agents=self.agents, agent=agent,
            ),
        )

        result = await harness.run("统计 2026 年 7 月毛利")

        self.assertEqual("completed", result.status.value)
        self.assertIn("reference_loaded", [event.type for event in result.events])
        self.assertEqual(
            ["nino.analyst", "nino.verifier"],
            [event.data["agent_id"] for event in result.events if event.type == "agent_started"],
        )

    async def test_lightweight_delegates_analyst_then_verifier(self) -> None:
        model = DemoChatModel()
        tools = DemoToolClient()
        references = ReferenceProvider()
        harness = OrchestratorHarness(
            model, self.skills, self.agents,
            lambda agent: ReActHarness(
                model, tools, self.skills, references=references,
                agents=self.agents, agent=agent,
            ),
        )

        result = await harness.run("复杂统计 2026 年 7 月毛利并核对结论")

        started = [event.data["agent_id"] for event in result.events if event.type == "agent_started"]
        completed = [event.data["agent_id"] for event in result.events if event.type == "agent_completed"]
        self.assertEqual("completed", result.status.value)
        self.assertEqual(["nino.analyst", "nino.verifier"], started)
        self.assertEqual(started, completed)

    @unittest.skipUnless(importlib.util.find_spec("langgraph"), "langgraph optional dependency")
    async def test_langgraph_delegates_with_same_agent_contract(self) -> None:
        model = DemoChatModel()
        tools = DemoToolClient()
        references = ReferenceProvider()
        harness = OrchestratorHarness(
            model, self.skills, self.agents,
            lambda agent: LangGraphReActHarness(
                model, tools, self.skills, references=references,
                agents=self.agents, agent=agent,
            ),
        )

        result = await harness.run("复杂统计 2026 年 7 月毛利并核对结论")

        started = [event.data["agent_id"] for event in result.events if event.type == "agent_started"]
        self.assertEqual("completed", result.status.value)
        self.assertEqual(["nino.analyst", "nino.verifier"], started)


if __name__ == "__main__":
    unittest.main()
