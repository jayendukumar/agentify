import json
from unittest.mock import AsyncMock

import pytest

from app.config import Settings
from app.ingestion.structuring import StructuringError, structure_process_from_image
from app.llm.client import LLMClient
from app.llm.types import ChatCompletionResult, Usage


def _settings() -> Settings:
    return Settings(_env_file=None, openrouter_api_key="sk-or-v1-test", llm_max_retries=1)


def _client_with_response(text: str | None, finish_reason: str = "stop") -> LLMClient:
    client = LLMClient(settings=_settings())
    client._client.chat.completions.create = AsyncMock()
    client.complete = AsyncMock(
        return_value=ChatCompletionResult(
            text=text,
            tool_calls=[],
            finish_reason=finish_reason,
            usage=Usage(input_tokens=10, output_tokens=10, total_tokens=20),
            model="qwen/qwen3.7-flash",
        )
    )
    return client


_VALID_PAYLOAD = {
    "actors": [],
    "elements": [
        {
            "id": "el-1",
            "type": "task",
            "label": "Submit request",
            "source_refs": [{"document_id": "wrong-id", "location": "image", "excerpt": "box labeled 'Submit request'"}],
            "confidence": "high",
        }
    ],
    "flows": [],
}


@pytest.mark.asyncio
async def test_structure_process_from_image_sends_image_content_block():
    client = _client_with_response(json.dumps(_VALID_PAYLOAD))

    schema = await structure_process_from_image(
        client,
        document_id="doc-correct",
        filename="flow.png",
        image_bytes=b"fake-png-bytes",
        mime_type="image/png",
        process_name="Whiteboard Process",
    )

    assert schema.process_name == "Whiteboard Process"
    assert len(schema.elements) == 1
    assert schema.elements[0].label == "Submit request"
    # document_id normalized regardless of what the model echoed back, same as the text path
    assert schema.elements[0].source_refs[0].document_id == "doc-correct"

    client.complete.assert_awaited_once()
    args, kwargs = client.complete.await_args
    assert kwargs["operation"] == "document_extraction"
    assert kwargs["response_format"] == {"type": "json_object"}

    messages = args[0]
    assert len(messages) == 1
    content_blocks = messages[0].content
    assert isinstance(content_blocks, list)
    kinds = {block.type for block in content_blocks}
    assert kinds == {"text", "image_url"}


@pytest.mark.asyncio
async def test_structure_process_from_image_raises_on_empty_response():
    client = _client_with_response(None)
    with pytest.raises(StructuringError, match="no content"):
        await structure_process_from_image(
            client,
            document_id="doc-1",
            filename="flow.png",
            image_bytes=b"x",
            mime_type="image/png",
            process_name="P",
        )


@pytest.mark.asyncio
async def test_structure_process_from_image_raises_on_truncation():
    client = _client_with_response(None, finish_reason="length")
    with pytest.raises(StructuringError, match="truncated at the token limit"):
        await structure_process_from_image(
            client,
            document_id="doc-1",
            filename="flow.png",
            image_bytes=b"x",
            mime_type="image/png",
            process_name="P",
        )
