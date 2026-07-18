from __future__ import annotations

import json
from typing import Any, Sequence

import httpx

from framework import Message, ModelTurn, ToolCall, ToolDefinition


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
        payload: dict[str, Any] = {
            "model": self._model,
            "messages": [self._message_payload(message) for message in messages],
            "temperature": 0,
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
            response = await self._client.post(
                self._endpoint,
                headers={"Authorization": f"Bearer {self._api_key}"},
                json=payload,
            )
            response.raise_for_status()
        except httpx.HTTPError as exc:
            status = exc.response.status_code if isinstance(exc, httpx.HTTPStatusError) else None
            detail = f" HTTP {status}" if status else ""
            raise OSError(f"Model request failed.{detail}") from exc
        body = response.json()
        try:
            message = body["choices"][0]["message"]
        except (KeyError, IndexError, TypeError) as exc:
            raise OSError("Model response does not contain choices[0].message.") from exc

        calls = tuple(self._tool_call(item) for item in message.get("tool_calls", ()))
        return ModelTurn(
            text=message.get("content") or "",
            tool_calls=calls,
            reasoning_content=message.get("reasoning_content") or None,
        )

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
