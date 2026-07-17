from __future__ import annotations

import json
from typing import Any, Sequence

from framework import Message, ModelTurn, ToolCall, ToolDefinition


class LangChainChatModel:
    """LangChain adapter that still satisfies the Runtime's framework-neutral ChatModel port."""

    def __init__(self, *, model: str, api_key: str, base_url: str) -> None:
        try:
            from langchain_openai import ChatOpenAI
        except ImportError as exc:
            raise RuntimeError(
                "LangChain adapter is not installed. Install the project with [langchain]."
            ) from exc
        self._model = ChatOpenAI(
            model=model,
            api_key=api_key,
            base_url=base_url,
            temperature=0,
        )

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        runnable = self._model.bind_tools([self._tool_payload(tool) for tool in tools])
        try:
            response = await runnable.ainvoke([self._message(item) for item in messages])
        except Exception as exc:
            raise OSError(f"LangChain model request failed: {type(exc).__name__}.") from exc
        content = response.content if isinstance(response.content, str) else json.dumps(
            response.content, ensure_ascii=False
        )
        calls = tuple(
            ToolCall(
                id=str(call.get("id") or f"call-{index}"),
                name=str(call["name"]),
                arguments=call.get("args") or {},
            )
            for index, call in enumerate(response.tool_calls)
        )
        return ModelTurn(
            text=content,
            tool_calls=calls,
            reasoning_content=response.additional_kwargs.get("reasoning_content"),
        )

    @staticmethod
    def _tool_payload(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": dict(tool.input_schema),
            },
        }

    @staticmethod
    def _message(message: Message) -> Any:
        from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

        if message.role == "system":
            return SystemMessage(content=message.content)
        if message.role == "user":
            return HumanMessage(content=message.content)
        if message.role == "tool":
            return ToolMessage(content=message.content, tool_call_id=message.tool_call_id or "")
        additional_kwargs = (
            {"reasoning_content": message.reasoning_content}
            if message.reasoning_content
            else {}
        )
        return AIMessage(
            content=message.content,
            additional_kwargs=additional_kwargs,
            tool_calls=[
                {"id": call.id, "name": call.name, "args": dict(call.arguments)}
                for call in message.tool_calls
            ],
        )
