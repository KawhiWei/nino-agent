from __future__ import annotations

import unittest
from typing import Sequence

from framework import ToolCall, ToolDefinition, ToolResult
from infrastructure.mcp import (
    McpServerConfig,
    McpServerRegistry,
    load_mcp_server_configs,
)


class FakeProvider:
    def __init__(self, *tools: str, error: Exception | None = None) -> None:
        self._tools = tuple(
            ToolDefinition(name, f"Tool {name}", {"type": "object"}) for name in tools
        )
        self._error = error
        self.calls: list[ToolCall] = []
        self.closed = False

    async def list_tools(self) -> Sequence[ToolDefinition]:
        if self._error is not None:
            raise self._error
        return self._tools

    async def invoke(self, call: ToolCall) -> ToolResult:
        self.calls.append(call)
        return ToolResult(f"result:{call.name}")

    async def close(self) -> None:
        self.closed = True


class McpServerConfigTests(unittest.TestCase):
    def test_loads_multiple_servers_and_legacy_fallback(self) -> None:
        configs = load_mcp_server_configs(
            '[{"id":"nino-data","url":"http://data/mcp"},'
            '{"id":"report","url":"http://report/mcp","required":false}]',
            "http://legacy/mcp",
        )
        fallback = load_mcp_server_configs("", "http://legacy/mcp")

        self.assertEqual(["nino-data", "report"], [item.id for item in configs])
        self.assertFalse(configs[1].required)
        self.assertEqual("nino-data", fallback[0].id)
        self.assertEqual("http://legacy/mcp", fallback[0].url)


class McpServerRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_aggregates_catalog_and_routes_to_owning_server(self) -> None:
        data = FakeProvider("nino_data_query_summary")
        report = FakeProvider("nino_report_render")
        registry = McpServerRegistry(
            (
                McpServerConfig("nino-data", "http://data/mcp"),
                McpServerConfig("report", "http://report/mcp"),
            ),
            {"nino-data": data, "report": report},
        )

        tools = await registry.list_tools()
        result = await registry.invoke(ToolCall("call-1", "nino_report_render", {}))

        self.assertEqual(
            ["nino_data_query_summary", "nino_report_render"],
            [item.name for item in tools],
        )
        self.assertEqual("result:nino_report_render", result.content)
        self.assertEqual([], data.calls)
        self.assertEqual(["nino_report_render"], [item.name for item in report.calls])
        self.assertEqual(["ready", "ready"], [item.state for item in registry.statuses])
        await registry.close()
        self.assertTrue(data.closed)
        self.assertTrue(report.closed)

    async def test_rejects_tool_name_collision(self) -> None:
        registry = McpServerRegistry(
            (
                McpServerConfig("one", "http://one/mcp"),
                McpServerConfig("two", "http://two/mcp"),
            ),
            {"one": FakeProvider("shared"), "two": FakeProvider("shared")},
        )

        with self.assertRaisesRegex(ValueError, "tool name collision"):
            await registry.list_tools()

    async def test_optional_server_failure_is_isolated(self) -> None:
        registry = McpServerRegistry(
            (
                McpServerConfig("required", "http://required/mcp"),
                McpServerConfig("optional", "http://optional/mcp", required=False),
            ),
            {
                "required": FakeProvider("available_tool"),
                "optional": FakeProvider(error=OSError("offline")),
            },
        )

        tools = await registry.list_tools()

        self.assertEqual(["available_tool"], [item.name for item in tools])
        self.assertEqual("unavailable", registry.statuses[1].state)

    async def test_required_server_failure_blocks_discovery(self) -> None:
        registry = McpServerRegistry(
            (McpServerConfig("required", "http://required/mcp"),),
            {"required": FakeProvider(error=OSError("offline"))},
        )

        with self.assertRaisesRegex(OSError, "Required MCP server discovery failed"):
            await registry.list_tools()


if __name__ == "__main__":
    unittest.main()
