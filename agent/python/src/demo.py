from __future__ import annotations

import json
import re
from typing import Sequence

from framework import Message, ModelTurn, ToolCall, ToolDefinition, ToolResult


class DemoToolClient:
    """In-memory tools for exercising Runtime behavior before the MCP adapter exists."""

    def __init__(self) -> None:
        self._tools = (
            ToolDefinition(
                name="nino_data_get_order_detail",
                description="Get one demo order and its calculated totals.",
                input_schema={
                    "type": "object",
                    "properties": {"order_serial_id": {"type": "string"}},
                    "required": ["order_serial_id"],
                    "additionalProperties": False,
                },
            ),
            ToolDefinition(
                name="nino_data_query_summary",
                description="Summarize paid, non-test demo orders by an approved dimension.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                        "group_by": {"enum": ["main_product_type", "channel", "day"]},
                    },
                    "required": ["start_date", "end_date", "group_by"],
                },
            ),
            ToolDefinition(
                name="nino_data_find_anomalies",
                description="Find the lowest-margin demo orders in a date range.",
                input_schema={
                    "type": "object",
                    "properties": {
                        "start_date": {"type": "string"},
                        "end_date": {"type": "string"},
                        "limit": {"type": "integer", "minimum": 1, "maximum": 20},
                    },
                    "required": ["start_date", "end_date", "limit"],
                },
            ),
        )

    async def list_tools(self) -> Sequence[ToolDefinition]:
        return self._tools

    async def invoke(self, call: ToolCall) -> ToolResult:
        payloads = {
            "nino_data_get_order_detail": {
                "order_serial_id": call.arguments.get("order_serial_id"),
                "currency": "CNY",
                "customer_sale_amount": 225,
                "net_supplier_cost": 165,
                "successful_refund_amount": 0,
                "demo_gross_margin": 60,
                "source": "runtime-demo-adapter"
            },
            "nino_data_query_summary": {
                "range": [call.arguments.get("start_date"), call.arguments.get("end_date")],
                "currency": "CNY",
                "groups": [
                    {"main_product_type": "AIR_TICKET", "order_count": 14, "demo_gross_margin": 660},
                    {"main_product_type": "CAR_SERVICE", "order_count": 12, "demo_gross_margin": 680},
                    {"main_product_type": "TRAIN_TICKET", "order_count": 12, "demo_gross_margin": 130}
                ],
                "source": "runtime-demo-adapter"
            },
            "nino_data_find_anomalies": {
                "currency": "CNY",
                "items": [
                    {"order_serial_id": "DEMO-202607-032", "demo_gross_margin": -450},
                    {"order_serial_id": "DEMO-202607-039", "demo_gross_margin": -180}
                ],
                "source": "runtime-demo-adapter"
            },
        }
        payload = payloads.get(call.name)
        if payload is None:
            return ToolResult(content=json.dumps({"error": "tool not found"}), is_error=True)
        return ToolResult(content=json.dumps(payload, ensure_ascii=False))


class DemoChatModel:
    """Scripted model used only to prove the ReAct state machine without an API key."""

    async def complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
    ) -> ModelTurn:
        question = next(message.content for message in reversed(messages) if message.role == "user")
        tool_names = {tool.name for tool in tools}
        system_text = "\n".join(message.content for message in messages if message.role == "system")
        is_orchestrator = "nino_runtime_dispatch_agent" in tool_names
        loaded_references: set[str] = set()
        for message in messages:
            if message.role != "tool":
                continue
            try:
                payload = json.loads(message.content)
            except json.JSONDecodeError:
                continue
            if payload.get("reference_id"):
                loaded_references.add(str(payload["reference_id"]))

        last = messages[-1]
        if last.role == "tool":
            data = json.loads(last.content)
            if data.get("kind") == "dispatch_result" and is_orchestrator:
                if data.get("agent_id") == "nino-data.analyst":
                    if any(word in question for word in ("复杂", "综合", "核对", "验证")):
                        return ModelTurn(tool_calls=(ToolCall(
                            id="demo-dispatch-verifier",
                            name="nino_runtime_dispatch_agent",
                            arguments={
                                "agent_id": "nino-data.verifier",
                                "skill_id": "nino-data.analysis",
                                "task": f"Independently verify this analysis for: {question}",
                                "context": str(data.get("summary", "")),
                            },
                        ),))
                return ModelTurn(text=f"演示主 Agent 结论：{data.get('summary', '')}")
            if data.get("reference_id"):
                return self._data_tool_turn(question)
            return ModelTurn(text=f"演示结论（CNY）：{json.dumps(data, ensure_ascii=False)}")

        data_intents = (
            "订单", "支付", "退款", "收入", "成本", "毛利", "亏损", "统计", "报表",
            "order", "payment", "refund", "margin", "nino data",
        )
        if is_orchestrator and any(word in question.lower() for word in data_intents):
            return ModelTurn(tool_calls=(ToolCall(
                id="demo-dispatch-analyst",
                name="nino_runtime_dispatch_agent",
                arguments={
                    "agent_id": "nino-data.analyst",
                    "skill_id": "nino-data.analysis",
                    "task": question,
                    "context": "Return a conclusion with deterministic tool evidence.",
                },
            ),))
        if is_orchestrator:
            return ModelTurn(text=f"演示通用回答：{question}")

        reference_id = self._reference_for(question)
        if "nino_runtime_load_reference" in tool_names and reference_id not in loaded_references:
            return ModelTurn(tool_calls=(ToolCall(
                id=f"demo-reference-{reference_id}",
                name="nino_runtime_load_reference",
                arguments={"reference_id": reference_id},
            ),))
        return self._data_tool_turn(question)

    @staticmethod
    def _reference_for(question: str) -> str:
        if any(word in question for word in ("亏损", "最低", "异常", "核对", "验证")):
            return "anomaly-rules"
        if any(word in question for word in ("统计", "汇总", "报表", "毛利")):
            return "metric-definitions"
        return "order-query-rules"

    @staticmethod
    def _data_tool_turn(question: str) -> ModelTurn:
        if any(word in question for word in ("亏损", "最低", "异常")):
            return ModelTurn(tool_calls=(ToolCall(
                id="demo-call-anomalies",
                name="nino_data_find_anomalies",
                arguments={"start_date": "2026-07-01", "end_date": "2026-08-01", "limit": 5},
            ),))
        if any(word in question for word in ("统计", "汇总", "报表")):
            return ModelTurn(tool_calls=(ToolCall(
                id="demo-call-summary",
                name="nino_data_query_summary",
                arguments={
                    "start_date": "2026-07-01",
                    "end_date": "2026-08-01",
                    "group_by": "main_product_type",
                },
            ),))

        match = re.search(r"DEMO-\d{6}-\d{3}", question, flags=re.IGNORECASE)
        order_serial_id = match.group(0).upper() if match else "DEMO-202607-001"
        return ModelTurn(tool_calls=(ToolCall(
            id="demo-call-order",
            name="nino_data_get_order_detail",
            arguments={"order_serial_id": order_serial_id},
        ),))
