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


async def evaluate_blueprint(llm: LLMClient, *, flow_nodes: list[FlowNodeInfo]) -> list[BlueprintNodeResult]:
    system_prompt = build_system_prompt(flow_nodes)

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

    try:
        payload: Any = json.loads(result.text)
        parsed = _ParsedOverlay.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise BlueprintServiceError(f"LLM returned invalid structured output for the blueprint: {exc}") from exc

    valid_ids = {node.id for node in flow_nodes}
    by_id = {node_result.node_id: node_result for node_result in parsed.nodes if node_result.node_id in valid_ids}
    missing = valid_ids - set(by_id)
    if missing:
        raise BlueprintServiceError(
            f"LLM response did not evaluate every node in the diagram -- missing: {sorted(missing)}"
        )

    return [by_id[node.id] for node in flow_nodes]
