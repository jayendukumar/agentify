import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx2
import openai
import pytest

from app.config import Settings
from app.llm.client import LLMClient
from app.llm.exceptions import (
    LLMAuthenticationError,
    LLMBadRequestError,
    LLMRateLimitError,
)
from app.llm.types import ChatMessage, ToolDefinition


def _settings(**overrides) -> Settings:
    defaults = dict(
        openrouter_api_key="sk-or-v1-test-key",
        llm_model="qwen/qwen3.7-flash",
        llm_max_retries=3,
    )
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


def _fake_response(*, text="hello", tool_calls=None, model="qwen/qwen3.7-flash", finish_reason="stop"):
    message = SimpleNamespace(content=text, tool_calls=tool_calls)
    choice = SimpleNamespace(message=message, finish_reason=finish_reason)
    usage = SimpleNamespace(prompt_tokens=10, completion_tokens=5, total_tokens=15)
    return SimpleNamespace(choices=[choice], usage=usage, model=model)


def _status_error(cls, status_code: int, message: str = "error"):
    request = httpx2.Request("POST", "https://openrouter.ai/api/v1/chat/completions")
    response = httpx2.Response(status_code, request=request)
    return cls(message, response=response, body=None)


@pytest.mark.asyncio
async def test_complete_returns_parsed_text_and_usage():
    client = LLMClient(settings=_settings())
    client._client.chat.completions.create = AsyncMock(return_value=_fake_response(text="hi there"))

    result = await client.complete(
        [ChatMessage(role="user", content="hello")], operation="test_op"
    )

    assert result.text == "hi there"
    assert result.tool_calls == []
    assert result.finish_reason == "stop"
    assert result.usage.input_tokens == 10
    assert result.usage.output_tokens == 5
    assert result.usage.total_tokens == 15
    assert result.model == "qwen/qwen3.7-flash"
    client._client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_complete_records_usage_on_client_tracker():
    client = LLMClient(settings=_settings())
    client._client.chat.completions.create = AsyncMock(return_value=_fake_response())

    await client.complete([ChatMessage(role="user", content="hello")], operation="test_op")
    await client.complete([ChatMessage(role="user", content="hello again")], operation="test_op")

    summary = client.usage_tracker.summary()
    assert summary["test_op"].calls == 2
    assert summary["test_op"].input_tokens == 20
    assert summary["test_op"].output_tokens == 10


@pytest.mark.asyncio
async def test_complete_parses_tool_calls():
    tool_call = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="lookup_actor", arguments=json.dumps({"name": "Finance"})),
    )
    client = LLMClient(settings=_settings())
    client._client.chat.completions.create = AsyncMock(
        return_value=_fake_response(text=None, tool_calls=[tool_call], finish_reason="tool_calls")
    )

    result = await client.complete(
        [ChatMessage(role="user", content="who owns this step?")],
        operation="test_op",
        tools=[ToolDefinition(name="lookup_actor", description="Look up an actor", parameters={})],
    )

    assert result.text is None
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "lookup_actor"
    assert result.tool_calls[0].arguments == {"name": "Finance"}


@pytest.mark.asyncio
async def test_authentication_error_raises_immediately_without_retry():
    client = LLMClient(settings=_settings())
    error = _status_error(openai.AuthenticationError, 401, "invalid api key")
    client._client.chat.completions.create = AsyncMock(side_effect=error)

    with pytest.raises(LLMAuthenticationError):
        await client.complete([ChatMessage(role="user", content="hi")], operation="test_op")

    client._client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_bad_request_error_raises_immediately_without_retry():
    client = LLMClient(settings=_settings())
    error = _status_error(openai.BadRequestError, 400, "bad schema")
    client._client.chat.completions.create = AsyncMock(side_effect=error)

    with pytest.raises(LLMBadRequestError):
        await client.complete([ChatMessage(role="user", content="hi")], operation="test_op")

    client._client.chat.completions.create.assert_awaited_once()


@pytest.mark.asyncio
async def test_rate_limit_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr("app.llm.client.asyncio.sleep", AsyncMock())

    client = LLMClient(settings=_settings(llm_max_retries=3))
    error = _status_error(openai.RateLimitError, 429, "rate limited")
    client._client.chat.completions.create = AsyncMock(
        side_effect=[error, _fake_response(text="ok on retry")]
    )

    result = await client.complete([ChatMessage(role="user", content="hi")], operation="test_op")

    assert result.text == "ok on retry"
    assert client._client.chat.completions.create.await_count == 2


@pytest.mark.asyncio
async def test_rate_limit_exhausts_retries(monkeypatch):
    monkeypatch.setattr("app.llm.client.asyncio.sleep", AsyncMock())

    client = LLMClient(settings=_settings(llm_max_retries=2))
    error = _status_error(openai.RateLimitError, 429, "rate limited")
    client._client.chat.completions.create = AsyncMock(side_effect=[error, error])

    with pytest.raises(LLMRateLimitError):
        await client.complete([ChatMessage(role="user", content="hi")], operation="test_op")

    assert client._client.chat.completions.create.await_count == 2
