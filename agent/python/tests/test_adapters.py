from __future__ import annotations

import json
import unittest

import httpx

from infrastructure.mcp import McpHttpToolClient
from infrastructure.openai_compatible import OpenAICompatibleChatModel
from framework import Message, ToolCall, ToolDefinition


class OpenAICompatibleChatModelTests(unittest.IsolatedAsyncioTestCase):
    async def test_round_trips_deepseek_reasoning_content_for_tool_calls(self) -> None:
        requests: list[dict] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            requests.append(body)
            if len(requests) == 1:
                return httpx.Response(200, json={"choices": [{"message": {
                    "content": "",
                    "reasoning_content": "I need the order tool.",
                    "tool_calls": [{
                        "id": "call-deepseek",
                        "type": "function",
                        "function": {"name": "order", "arguments": '{"id":"1"}'},
                    }],
                }}]})
            return httpx.Response(200, json={"choices": [{"message": {
                "content": "final answer",
                "reasoning_content": "The tool result is sufficient.",
            }}]})

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAICompatibleChatModel(
            model="deepseek-v4-pro",
            api_key="test-key",
            base_url="https://api.deepseek.com",
            thinking_mode="enabled",
            reasoning_effort="high",
            client=client,
        )
        tools = (ToolDefinition("order", "Get order", {"type": "object"}),)
        first = await model.complete((Message(role="user", content="order 1"),), tools)
        second = await model.complete((
            Message(role="user", content="order 1"),
            Message(
                role="assistant",
                tool_calls=first.tool_calls,
                reasoning_content=first.reasoning_content,
            ),
            Message(role="tool", content='{"found":true}', tool_call_id="call-deepseek"),
        ), tools)

        self.assertEqual("I need the order tool.", first.reasoning_content)
        self.assertEqual("final answer", second.text)
        self.assertEqual({"type": "enabled"}, requests[0]["thinking"])
        self.assertEqual("high", requests[0]["reasoning_effort"])
        self.assertEqual(
            "I need the order tool.", requests[1]["messages"][1]["reasoning_content"]
        )
        await client.aclose()

    async def test_converts_tools_and_structured_tool_calls(self) -> None:
        async def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            self.assertEqual("demo-model", body["model"])
            self.assertEqual("nino_data_get_order_detail", body["tools"][0]["function"]["name"])
            return httpx.Response(200, json={
                "choices": [{"message": {
                    "content": "",
                    "tool_calls": [{
                        "id": "call-1",
                        "type": "function",
                        "function": {
                            "name": "nino_data_get_order_detail",
                            "arguments": '{"orderSerialId":"DEMO-202607-001"}',
                        },
                    }],
                }}],
            })

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        model = OpenAICompatibleChatModel(
            model="demo-model", api_key="test-key", base_url="https://model.test/v1",
            client=client,
        )
        result = await model.complete(
            (Message(role="user", content="查询订单"),),
            (ToolDefinition(
                "nino_data_get_order_detail", "Get order", {"type": "object"}
            ),),
        )

        self.assertEqual("nino_data_get_order_detail", result.tool_calls[0].name)
        self.assertEqual("DEMO-202607-001", result.tool_calls[0].arguments["orderSerialId"])
        await client.aclose()


class McpHttpToolClientTests(unittest.IsolatedAsyncioTestCase):
    async def test_initializes_lists_and_calls_streamable_http_tools(self) -> None:
        methods: list[str] = []

        async def handle(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            methods.append(body["method"])
            if body["method"] == "notifications/initialized":
                return httpx.Response(202)
            if body["method"] == "initialize":
                result = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}}
            elif body["method"] == "tools/list":
                result = {"tools": [{
                    "name": "nino_data_get_order_detail",
                    "description": "Get order",
                    "inputSchema": {"type": "object"},
                }]}
            else:
                result = {"content": [{"type": "text", "text": '{"found":true}'}]}
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result})
            return httpx.Response(
                200, text=f"event: message\ndata: {payload}\n\n",
                headers={"content-type": "text/event-stream"},
            )

        client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
        tools = McpHttpToolClient("https://mcp.test/mcp", client=client)

        listed = await tools.list_tools()
        result = await tools.invoke(ToolCall("call-1", listed[0].name, {"id": "1"}))

        self.assertEqual(
            ["initialize", "notifications/initialized", "tools/list", "tools/call"], methods
        )
        self.assertEqual('{"found":true}', result.content)
        self.assertFalse(result.is_error)
        await client.aclose()


if __name__ == "__main__":
    unittest.main()
