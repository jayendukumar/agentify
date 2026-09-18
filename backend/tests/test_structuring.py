import json
from unittest.mock import AsyncMock

import pytest

from app.ingestion.extractors import ExtractedBlock
from app.ingestion.structuring import StructuringError, structure_process
from app.llm.types import ChatCompletionResult, Usage
from app.llm.client import LLMClient
from app.config import Settings


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


_BLOCKS = [ExtractedBlock(kind="text", location="page 1", content="Step 1: Submit the request form.")]


@pytest.mark.asyncio
async def test_structure_process_parses_valid_response():
    payload = {
        "actors": [{"id": "actor-1", "name": "Requester", "type": "role"}],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "Submit the request form",
                "actor_id": "actor-1",
                "inputs": [],
                "outputs": ["request form"],
                "systems_touched": [],
                "source_refs": [{"document_id": "doc-1", "location": "page 1", "excerpt": "Submit the request form."}],
                "confidence": "high",
            }
        ],
        "flows": [],
    }
    client = _client_with_response(json.dumps(payload))

    schema = await structure_process(
        client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="Onboarding"
    )

    assert schema.process_name == "Onboarding"
    assert len(schema.actors) == 1
    assert schema.actors[0].name == "Requester"
    assert len(schema.elements) == 1
    assert schema.elements[0].confidence == "high"
    assert schema.elements[0].source_refs[0].document_id == "doc-1"

    client.complete.assert_awaited_once()
    _, kwargs = client.complete.await_args
    assert kwargs["operation"] == "document_extraction"
    assert kwargs["response_format"] == {"type": "json_object"}


@pytest.mark.asyncio
async def test_structure_process_overwrites_document_id_regardless_of_llm_output():
    # The LLM is told the document_id in the prompt but can't be trusted to
    # echo it back correctly -- structure_process must set it deterministically.
    payload = {
        "actors": [],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "Submit the request form",
                "source_refs": [{"document_id": "wrong-id", "location": "page 1", "excerpt": "x"}],
                "confidence": "high",
            }
        ],
        "flows": [],
    }
    client = _client_with_response(json.dumps(payload))

    schema = await structure_process(
        client, document_id="doc-correct", filename="sop.pdf", blocks=_BLOCKS, process_name="P"
    )

    assert schema.elements[0].source_refs[0].document_id == "doc-correct"


@pytest.mark.asyncio
async def test_structure_process_ids_are_globally_unique_across_calls():
    # The LLM assigns ids like "el-1"/"actor-1" that are only unique within
    # one response -- two different documents (two separate calls) both
    # legitimately produce "el-1" for completely different steps. If that id
    # survives unchanged, merging two documents' schemas (or persisting them
    # to a real DB primary key) silently collides two unrelated things.
    payload = {
        "actors": [{"id": "actor-1", "name": "Requester", "type": "role"}],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "Step from doc A",
                "actor_id": "actor-1",
                "source_refs": [{"document_id": "d", "location": "page 1", "excerpt": "x"}],
                "confidence": "high",
            }
        ],
        "flows": [{"id": "f-1", "from": "el-1", "to": "el-1", "condition": None}],
    }

    client_a = _client_with_response(json.dumps(payload))
    schema_a = await structure_process(client_a, document_id="doc-a", filename="a.pdf", blocks=_BLOCKS, process_name="P")

    client_b = _client_with_response(json.dumps(payload))
    schema_b = await structure_process(client_b, document_id="doc-b", filename="b.pdf", blocks=_BLOCKS, process_name="P")

    assert schema_a.elements[0].id != schema_b.elements[0].id
    assert schema_a.actors[0].id != schema_b.actors[0].id
    # references within one schema must be rewritten consistently, not just
    # the top-level id
    assert schema_a.elements[0].actor_id == schema_a.actors[0].id
    assert schema_a.flows[0].from_ == schema_a.elements[0].id
    assert schema_a.flows[0].to == schema_a.elements[0].id


@pytest.mark.asyncio
async def test_structure_process_drops_element_reference_to_undeclared_actor():
    # Real failure found against a real, complex multi-actor document: the
    # model referenced actor_id "actor-it-support" on an element without
    # that actor ever appearing in the "actors" array. Left as-is, the
    # stale local id survives remapping unchanged and violates
    # process_elements' actor_id FK the moment this schema is persisted --
    # see app.db.repository._replace_schema_rows.
    payload = {
        "actors": [{"id": "actor-1", "name": "HR", "type": "role"}],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "Undeclared-actor step",
                "actor_id": "actor-it-support",  # never declared above
                "source_refs": [{"document_id": "d", "location": "page 1", "excerpt": "x"}],
                "confidence": "high",
            }
        ],
        "flows": [],
    }
    client = _client_with_response(json.dumps(payload))

    schema = await structure_process(client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="P")

    assert schema.elements[0].actor_id is None


@pytest.mark.asyncio
async def test_structure_process_drops_flow_referencing_undeclared_element():
    payload = {
        "actors": [],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "Real step",
                "source_refs": [{"document_id": "d", "location": "page 1", "excerpt": "x"}],
                "confidence": "high",
            }
        ],
        "flows": [
            {"id": "f-1", "from": "el-1", "to": "el-99", "condition": None},  # el-99 never declared
        ],
    }
    client = _client_with_response(json.dumps(payload))

    schema = await structure_process(client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="P")

    assert schema.flows == []
    assert len(schema.elements) == 1  # the valid element itself is untouched


@pytest.mark.asyncio
async def test_structure_process_strips_source_marker_from_location():
    # A real live run showed the model sometimes copies the whole
    # "[SOURCE paragraph 1]" marker instead of just "paragraph 1" -- this
    # must be normalized regardless of what the model actually returns.
    payload = {
        "actors": [],
        "elements": [
            {
                "id": "el-1",
                "type": "task",
                "label": "x",
                "source_refs": [{"document_id": "d", "location": "[SOURCE paragraph 1]", "excerpt": "x"}],
                "confidence": "high",
            }
        ],
        "flows": [],
    }
    client = _client_with_response(json.dumps(payload))

    schema = await structure_process(client, document_id="d", filename="sop.pdf", blocks=_BLOCKS, process_name="P")

    assert schema.elements[0].source_refs[0].location == "paragraph 1"


@pytest.mark.asyncio
async def test_structure_process_raises_truncation_specific_error():
    # A real live run against a larger document hit this exact case:
    # output_tokens == max_tokens, empty text, finish_reason "length" --
    # the model spent its whole budget on reasoning before answering.
    client = _client_with_response(None, finish_reason="length")
    with pytest.raises(StructuringError, match="truncated at the token limit"):
        await structure_process(client, document_id="doc-1", filename="big.pdf", blocks=_BLOCKS, process_name="P")


@pytest.mark.asyncio
async def test_structure_process_raises_on_empty_blocks():
    client = _client_with_response("{}")
    with pytest.raises(StructuringError, match="No extractable text"):
        await structure_process(client, document_id="doc-1", filename="empty.pdf", blocks=[], process_name="P")
    client.complete.assert_not_awaited()


@pytest.mark.asyncio
async def test_structure_process_raises_on_invalid_json():
    client = _client_with_response("not valid json")
    with pytest.raises(StructuringError, match="invalid structured output"):
        await structure_process(client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="P")


@pytest.mark.asyncio
async def test_structure_process_raises_on_schema_mismatch():
    # "type" is not one of the allowed element types -- should fail pydantic validation
    payload = {
        "actors": [],
        "elements": [{"id": "el-1", "type": "not-a-real-type", "label": "x", "source_refs": [], "confidence": "high"}],
        "flows": [],
    }
    client = _client_with_response(json.dumps(payload))
    with pytest.raises(StructuringError, match="invalid structured output"):
        await structure_process(client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="P")


@pytest.mark.asyncio
async def test_structure_process_raises_on_empty_llm_response():
    client = _client_with_response(None)
    with pytest.raises(StructuringError, match="no content"):
        await structure_process(client, document_id="doc-1", filename="sop.pdf", blocks=_BLOCKS, process_name="P")
