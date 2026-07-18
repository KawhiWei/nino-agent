from __future__ import annotations

import inspect
import json
from typing import Any, Awaitable, Callable, Sequence

import httpx

from framework import Message, ModelTurn, ToolCall, ToolDefinition


TextDeltaHandler = Callable[[str], Awaitable[Any] | Any]


class OpenAICompatibleChatModel:
    """Small dependency-free model adapter for OpenAI-compatible chat APIs."""

    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://api.openai.com/v1",
        timeout_seconds: float = 60.0,
        thinking_mode: str = "",
        reasoning_effort: str = "",
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not model.strip():
            raise ValueError("A model name is required.")
        if not api_key.strip():
            raise ValueError("OPENAI_API_KEY is required.")
        if not base_url.strip():
            raise ValueError("INCERRY_OPENAI_BASE_URL is required.")
        if timeout_seconds <= 0:
            raise ValueError("NINO_MODEL_TIMEOUT_SECONDS must be positive.")
        self._model = model.strip()
        self._api_key = api_key.strip()
        self._endpoint = f"{base_url.strip().rstrip('/')}/chat/completions"
        if thinking_mode not in {"", "enabled", "disabled"}:
            raise ValueError("NINO_MODEL_THINKING must be enabled, disabled, or empty.")
        self._thinking_mode = thinking_mode
        self._reasoning_effort = reasoning_effort.strip()
        self._client = client or httpx.AsyncClient(timeout=timeout_seconds)

    async def complete(
        self, messages: Sequence[Message], tools: Sequence[ToolDefinition]
    ) -> ModelTurn:
        return await self.stream_complete(messages, tools)

    async def stream_complete(
        self,
        messages: Sequence[Message],
        tools: Sequence[ToolDefinition],
        on_text_delta: TextDeltaHandler | None = None,
    ) -> ModelTurn:
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": 0,
            "stream": True,
        }
        if self._thinking_mode:
            payload["thinking"] = {"type": self._thinking_mode}
        if self._reasoning_effort:
            payload["reasoning_effort"] = self._reasoning_effort
        if tools:
            payload["tools"] = [
                {
                    "type": "function",
                    "function": {
                        "name": tool.name,
                        "description": tool.description,
                        "parameters": dict(tool.input_schema),
                    },
                }
                for tool in tools
            ]
            payload["tool_choice"] = "auto"

        try:
            async with self._client.stream(
                "POST",
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            ) as response:
                response.raise_for_status()
                if "text/event-stream" in response.headers.get("content-type", ""):
                    return await self._stream_turn(response, on_text_delta)
                body = json.loads(await response.aread())
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f" HTTP {status}" if status else ""
            raise OSError(f"Model request failed.{detail}") from exc
        except json.JSONDecodeError as exc:
            raise OSError("Model response is neither valid SSE nor JSON.") from exc
        return self._turn_from_body(body)

    async def _stream_turn(
        self,
        response: httpx.Response,
        on_text_delta: TextDeltaHandler | None,
    ) -> ModelTurn:
        text_parts: list[str] = []
        reasoning_parts: list[str] = []
        calls: dict[int, dict[str, str]] = {}
        try:
            async for line in response.aiter_lines():
                stripped = line.strip()
                if not stripped or stripped.startswith(":") or not stripped.startswith("data:"):
                    continue
                data = stripped[5:].strip()
                if data == "[DONE]":
                    break
                chunk = json.loads(data)
                if chunk.get("error"):
                    raise OSError("Model stream returned an error payload.")
                choices = chunk.get("choices") or ()
                if not choices:
                    continue
                delta = choices[0].get("delta") or choices[0].get("message") or {}
                content = delta.get("content")
                if isinstance(content, str) and content:
                    text_parts.append(content)
                    if on_text_delta is not None:
                        result = on_text_delta(content)
                        if inspect.isawaitable(result):
                            await result
                reasoning = delta.get("reasoning_content")
                if isinstance(reasoning, str) and reasoning:
                    reasoning_parts.append(reasoning)
                for item in delta.get("tool_calls") or ():
                    index = int(item.get("index", 0))
                    state = calls.setdefault(
                        index, {"id": "", "name": "", "arguments": ""}
                    )
                    if item.get("id"):
                        state["id"] = str(item["id"])
                    function = item.get("function") or {}
                    if function.get("name"):
                        state["name"] += str(function["name"])
                    if function.get("arguments"):
                        state["arguments"] += str(function["arguments"])
        except (json.JSONDecodeError, TypeError, ValueError) as exc:
            raise OSError("Model returned an invalid SSE stream.") from exc

        tool_calls = tuple(
            self._stream_tool_call(index, value)
            for index, value in sorted(calls.items())
        )
        return ModelTurn(
            text="".join(text_parts),
            tool_calls=tool_calls,
            reasoning_content="".join(reasoning_parts) or None,
        )

    @classmethod
    def _turn_from_body(cls, body: Any) -> ModelTurn:
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OSError("Model response does not contain choices[0].message.") from exc

        calls = tuple(cls._tool_call(item) for item in message.get("tool_calls", ()))
        return ModelTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            reasoning_content=message.get("reasoning_content") or None,
        )

    @staticmethod
    def _stream_tool_call(index: int, payload: dict[str, str]) -> ToolCall:
        try:
            arguments = json.loads(payload["arguments"] or "{}")
            if not payload["name"] or not isinstance(arguments, dict):
                raise ValueError("Incomplete streamed Tool call.")
            return ToolCall(
                payload["id"] or f"call-{index}", payload["name"], arguments
            )
        except (json.JSONDecodeError, ValueError) as exc:
            raise OSError("Model returned an invalid streamed Tool call.") from exc

    @staticmethod
    def _message_payload(message: Message) -> dict[str, Any]:
        payload: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            payload["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {
                        "name": call.name,
                        "arguments": json.dumps(call.arguments, ensure_ascii=False),
                    },
                }
                for call in message.tool_calls
            ]
        if message.tool_call_id:
            payload["tool_call_id"] = message.tool_call_id
        if message.reasoning_content:
            payload["reasoning_content"] = message.reasoning_content
        return payload

    @staticmethod
    def _tool_call(payload: dict[str, Any]) -> ToolCall:
        try:
            function = payload["function"]
            arguments = json.loads(function.get("arguments") or "{}")
            if not isinstance(arguments, dict):
                raise ValueError("Tool arguments must be a JSON object.")
            return ToolCall(str(payload["id"]), str(function["name"]), arguments)
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise OSError("Model returned an invalid structured tool call.") from exc
