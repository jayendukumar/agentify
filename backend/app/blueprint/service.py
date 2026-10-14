"""Epic 7: orchestrates one blueprint generation run -- call the LLM once
for the whole diagram (not per-node -- US7.5's consolidation
recommendations need cross-node context anyway), parse+validate its JSON
reply into BlueprintNodeResult per node, and defensively reconcile node
coverage. Never trust the LLM's node_id list as-is, same principle as
app/ingestion/structuring.py's id remapping: drop results for ids that
don't exist in this diagram, then require every real node to have gotten
exactly one result -- a blueprint that silently skips a node contradicts
US7.1 ("every node ... evaluated").
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from pydantic import BaseModel, ValidationError

from app.bpmn.nodes import FlowNodeInfo
from app.llm import ChatMessage, LLMClient
from app.schemas.blueprint import BlueprintNodeResult

from .prompts import build_system_prompt


class BlueprintServiceError(Exception):
    """Raised when the LLM's reply can't be parsed into the expected
    shape, or doesn't cover every node in the diagram -- the API layer
    turns this into a clean error response rather than a 500 crash
    (mirrors app/chat/service.py's ChatServiceError)."""


class _ParsedOverlay(BaseModel):
    nodes: list[BlueprintNodeResult]


# Epic 36 (found seeding a 20-node process with a 31-rule context pack):
# one call for the whole diagram ran past the output token cap and came
# back truncated mid-JSON. Larger diagrams are evaluated in chunks, each
# call seeing the whole diagram for context; consolidation stays within a
# chunk (see planning/decision-log.md, 2026-10-13).
CHUNK_SIZE = 8


def _chunks(flow_nodes: list[FlowNodeInfo]) -> list[list[FlowNodeInfo]]:
    if len(flow_nodes) <= CHUNK_SIZE + 2:
        return [flow_nodes]
    return [flow_nodes[i : i + CHUNK_SIZE] for i in range(0, len(flow_nodes), CHUNK_SIZE)]


async def _evaluate_chunk(
    llm: LLMClient, chunk: list[FlowNodeInfo], flow_nodes: list[FlowNodeInfo], context_text: str
) -> list[BlueprintNodeResult]:
    """One retry on malformed output: with several chunks per blueprint, one
    occasionally-invalid reply (seen live: a missing comma mid-JSON) would
    otherwise fail the whole generation."""
    try:
        return await _evaluate_chunk_once(llm, chunk, flow_nodes, context_text)
    except _MalformedReply:
        try:
            return await _evaluate_chunk_once(llm, chunk, flow_nodes, context_text)
        except _MalformedReply as exc:
            raise BlueprintServiceError(str(exc)) from exc


class _MalformedReply(BlueprintServiceError):
    pass


async def _evaluate_chunk_once(
    llm: LLMClient, chunk: list[FlowNodeInfo], flow_nodes: list[FlowNodeInfo], context_text: str
) -> list[BlueprintNodeResult]:
    rest = [n for n in flow_nodes if n not in chunk]
    system_prompt = build_system_prompt(chunk, context_text, context_nodes=rest or None)

    result = await llm.complete(
        [ChatMessage(role="system", content=system_prompt)],
        operation="blueprint_evaluation",
        response_format={"type": "json_object"},
        # Same headroom as chat_edit/document_extraction -- Qwen3.7 Flash
        # spends part of this budget on internal reasoning, and a full
        # diagram's worth of nodes each needing a rationale + agent spec is
        # a large JSON payload.
        max_tokens=16000,
    )

    if not result.text:
        raise BlueprintServiceError("LLM returned no content while evaluating the blueprint")
    if result.finish_reason == "length":
        raise BlueprintServiceError(
            f"The blueprint for {len(chunk)} steps was cut off at the output limit -- lower CHUNK_SIZE in "
            "app/blueprint/service.py"
        )

    try:
        payload: Any = json.loads(result.text)
        parsed = _ParsedOverlay.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise _MalformedReply(f"LLM returned invalid structured output for the blueprint: {exc}") from exc

    chunk_ids = {node.id for node in chunk}
    results = [r for r in parsed.nodes if r.node_id in chunk_ids]
    for r in results:
        # Consolidation can't reach outside the chunk the model evaluated.
        if r.agent_spec is not None and r.agent_spec.consolidated_from_nodes:
            r.agent_spec.consolidated_from_nodes = [n for n in r.agent_spec.consolidated_from_nodes if n in chunk_ids]
    return results


async def evaluate_blueprint(
    llm: LLMClient, *, flow_nodes: list[FlowNodeInfo], context_text: str = ""
) -> list[BlueprintNodeResult]:
    parts = await asyncio.gather(*(_evaluate_chunk(llm, chunk, flow_nodes, context_text) for chunk in _chunks(flow_nodes)))

    valid_ids = {node.id for node in flow_nodes}
    by_id = {node_result.node_id: node_result for part in parts for node_result in part if node_result.node_id in valid_ids}
    missing = valid_ids - set(by_id)
    if missing:
        raise BlueprintServiceError(
            f"LLM response did not evaluate every node in the diagram -- missing: {sorted(missing)}"
        )

    return [by_id[node.id] for node in flow_nodes]
