from __future__ import annotations

import asyncio
import json
import logging
from functools import lru_cache
from typing import Any

import openai

from app.config import Settings, get_settings

from . import metering
from .exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMError,
    LLMPolicyError,
    LLMRateLimitError,
    LLMServerError,
    LLMTimeoutError,
)
from .routing import LlmRoute, current_route, redact
from .types import ChatCompletionResult, ChatMessage, TextContent, ToolCall, ToolDefinition, Usage
from .usage import UsageTracker

logger = logging.getLogger("app.llm")

# APITimeoutError is itself a subclass of APIConnectionError; both are listed
# for clarity about what this layer treats as safe to retry.
_RETRYABLE_EXCEPTIONS = (
    openai.RateLimitError,
    openai.APITimeoutError,
    openai.APIConnectionError,
    openai.InternalServerError,
)


class LLMClient:
    """Provider-agnostic chat-completion client for backend features.

    Currently backed by OpenRouter's OpenAI-compatible API, routing to
    Qwen3.7 Flash by default (see planning/claude-api-access-notes.md for
    why). Callers should depend on this class -- not on `openai` or
    OpenRouter directly -- so a future provider swap stays contained here.
    """

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()

        default_headers: dict[str, str] = {}
        if self._settings.llm_site_url:
            default_headers["HTTP-Referer"] = self._settings.llm_site_url
        if self._settings.llm_app_name:
            default_headers["X-Title"] = self._settings.llm_app_name

        self._client = openai.AsyncOpenAI(
            api_key=self._settings.openrouter_api_key,
            base_url=self._settings.llm_base_url,
            timeout=self._settings.llm_request_timeout_seconds,
            default_headers=default_headers or None,
        )

        log_path = self._settings.usage_log_path if self._settings.llm_usage_log_enabled else None
        self.usage_tracker = UsageTracker(log_path=log_path)
        self._routed_clients: dict[tuple[str, str | None], openai.AsyncOpenAI] = {}
        self._slots: dict[tuple[str, str], asyncio.Semaphore] = {}

    def _slot(self, base_url: str, model: str) -> asyncio.Semaphore:
        """Epic 34, US34.4: per endpoint+model concurrency bound -- distinct
        from the twin's *simulated* 429 faults, which hit the agent's tools,
        not its own model calls."""
        key = (base_url, model)
        if key not in self._slots:
            self._slots[key] = asyncio.Semaphore(self._settings.llm_max_concurrency)
        return self._slots[key]

    def _mark_cacheable(self, payload: dict[str, Any]) -> None:
        """Epic 34, US34.5: across a batch, the system prompt (agent
        instructions, organization context) is identical for every row. For
        providers that need an explicit marker (Anthropic models, including
        via OpenRouter), mark it cacheable; OpenAI-compatible providers that
        cache prefixes automatically need nothing -- keeping the stable part
        first, as every call site here already does, is what they need."""
        model = str(payload.get("model", "")).lower()
        if not self._settings.llm_prompt_cache or not ("anthropic/" in model or model.startswith("claude")):
            return
        first = payload["messages"][0] if payload.get("messages") else None
        if first and first.get("role") == "system" and isinstance(first.get("content"), str):
            first["content"] = [{"type": "text", "text": first["content"], "cache_control": {"type": "ephemeral"}}]

    def _client_for(self, route: LlmRoute | None) -> openai.AsyncOpenAI:
        """Epic 31, US31.8: a self-hosted OpenAI-compatible endpoint when the
        business unit's policy names one, else the default provider."""
        if route is None or not route.base_url:
            return self._client
        key = (route.base_url, route.api_key_env)
        if key not in self._routed_clients:
            import os

            self._routed_clients[key] = openai.AsyncOpenAI(
                api_key=os.environ.get(route.api_key_env or "", "") or "not-needed",
                base_url=route.base_url,
                timeout=self._settings.llm_request_timeout_seconds,
            )
        return self._routed_clients[key]

    @staticmethod
    def _redacted(messages: list[ChatMessage]) -> list[ChatMessage]:
        result = []
        for message in messages:
            content = message.content
            if isinstance(content, str):
                content = redact(content)
            elif isinstance(content, list):
                content = [TextContent(text=redact(c.text)) if isinstance(c, TextContent) else c for c in content]
            result.append(message.model_copy(update={"content": content}))
        return result

    async def complete(
        self,
        messages: list[ChatMessage],
        *,
        operation: str,
        tools: list[ToolDefinition] | None = None,
        response_format: dict[str, Any] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        model: str | None = None,
    ) -> ChatCompletionResult:
        """Run one chat completion and return a parsed result.

        `operation` is a short label (e.g. "document_extraction",
        "bpmn_generation", "chat_edit", "blueprint_evaluation") logged
        alongside token usage, so Epic 10 (US10.3) cost tracking can
        attribute spend back to a call site.

        Epic 31, US31.8: honours the business-unit LLM policy in effect
        (app/llm/routing.py) -- refuses a disallowed provider, routes to a
        self-hosted endpoint, and redacts personal data when required.

        Epic 38, US38.9: inside a tenant, refused once its monthly budget is
        spent (LLMBudgetError), held to its requests per minute, and
        recorded as its usage (app/llm/metering.py).
        """
        route = current_route.get()
        if route is not None and route.blocked_reason:
            raise LLMPolicyError(route.blocked_reason)
        # Epic 38, US38.9: the tenant's budget and requests per minute.
        await metering.admit()
        if route is not None and route.redact_pii:
            messages = self._redacted(messages)
        if route is not None and route.model:
            model = route.model
        client = self._client_for(route)
        payload = self._build_payload(
            messages,
            tools=tools,
            response_format=response_format,
            temperature=temperature,
            max_tokens=max_tokens,
            model=model,
        )

        self._mark_cacheable(payload)
        slot = self._slot(str(client.base_url), str(payload.get("model")))
        last_error: Exception | None = None
        for attempt in range(1, self._settings.llm_max_retries + 1):
            try:
                async with slot:
                    response = await client.chat.completions.create(**payload)
                result = self._parse_response(response, operation=operation)
                await metering.record(operation=operation, model=result.model, usage=result.usage)
                return result
            except openai.AuthenticationError as exc:
                raise LLMAuthenticationError(str(exc)) from exc
            except openai.BadRequestError as exc:
                raise LLMBadRequestError(str(exc)) from exc
            except _RETRYABLE_EXCEPTIONS as exc:
                last_error = exc
                if attempt == self._settings.llm_max_retries:
                    break
                wait_seconds = min(2 ** (attempt - 1), 20)
                logger.warning(
                    "llm_call_retrying",
                    extra={
                        "operation": operation,
                        "attempt": attempt,
                        "max_retries": self._settings.llm_max_retries,
                        "error": str(exc),
                    },
                )
                await asyncio.sleep(wait_seconds)
            except openai.APIError as exc:
                raise LLMError(str(exc)) from exc

        assert last_error is not None  # loop above always sets this before falling through
        if isinstance(last_error, openai.RateLimitError):
            raise LLMRateLimitError(str(last_error)) from last_error
        if isinstance(last_error, (openai.APITimeoutError, openai.APIConnectionError)):
            raise LLMTimeoutError(str(last_error)) from last_error
        raise LLMServerError(str(last_error)) from last_error

    def _build_payload(
        self,
        messages: list[ChatMessage],
        *,
        tools: list[ToolDefinition] | None,
        response_format: dict[str, Any] | None,
        temperature: float | None,
        max_tokens: int | None,
        model: str | None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": model or self._settings.llm_model,
            "messages": [self._message_to_openai(m) for m in messages],
        }
        if tools:
            payload["tools"] = [self._tool_to_openai(t) for t in tools]
        if response_format is not None:
            payload["response_format"] = response_format
        if temperature is not None:
            payload["temperature"] = temperature
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        return payload

    @staticmethod
    def _message_to_openai(message: ChatMessage) -> dict[str, Any]:
        content: Any
        if isinstance(message.content, str):
            content = message.content
        else:
            content = [block.model_dump() for block in message.content]

        entry: dict[str, Any] = {"role": message.role, "content": content}
        if message.tool_call_id:
            entry["tool_call_id"] = message.tool_call_id
        if message.name:
            entry["name"] = message.name
        if message.tool_calls:
            entry["tool_calls"] = [
                {
                    "id": call.id,
                    "type": "function",
                    "function": {"name": call.name, "arguments": json.dumps(call.arguments)},
                }
                for call in message.tool_calls
            ]
        return entry

    @staticmethod
    def _tool_to_openai(tool: ToolDefinition) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": tool.name,
                "description": tool.description,
                "parameters": tool.parameters,
            },
        }

    def _parse_response(self, response: Any, *, operation: str) -> ChatCompletionResult:
        choice = response.choices[0]
        message = choice.message

        tool_calls: list[ToolCall] = []
        for call in getattr(message, "tool_calls", None) or []:
            try:
                arguments = json.loads(call.function.arguments)
            except (json.JSONDecodeError, TypeError):
                arguments = {}
            tool_calls.append(ToolCall(id=call.id, name=call.function.name, arguments=arguments))

        usage_obj = response.usage
        usage = Usage(
            input_tokens=usage_obj.prompt_tokens if usage_obj else 0,
            output_tokens=usage_obj.completion_tokens if usage_obj else 0,
            total_tokens=usage_obj.total_tokens if usage_obj else 0,
        )

        logger.info(
            "llm_call_completed",
            extra={"operation": operation, "model": response.model, "finish_reason": choice.finish_reason},
        )
        self.usage_tracker.record(operation=operation, model=response.model, usage=usage)

        return ChatCompletionResult(
            text=message.content,
            tool_calls=tool_calls,
            finish_reason=choice.finish_reason,
            usage=usage,
            model=response.model,
        )


@lru_cache
def get_llm_client() -> LLMClient:
    return LLMClient()
