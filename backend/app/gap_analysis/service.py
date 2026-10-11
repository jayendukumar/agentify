"""Epic 11: one gap-analysis run -- call the LLM once for the whole
process schema (cross-document conflicts need to see every document's
elements together, same reasoning as blueprint's US7.5 consolidation),
parse its JSON reply into proposed findings, and defensively reconcile
against the real schema. Never trust the LLM's id list as-is, same
principle as app/ingestion/structuring.py/app/blueprint/service.py: drop
any finding referencing an id that doesn't exist in this schema. Unlike
blueprint, there is no full node-coverage requirement here -- most
elements have no gap, so an empty findings list is a normal, expected
result.

run_gap_analysis is the orchestration layer (LLM call + DB reconciliation)
-- lives here rather than app/db/repository.py since it composes an LLM
call with repository reads/writes, the same "service function does the
I/O-plus-LLM orchestration" split app/ingestion/pipeline.py already uses.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.context.resolver import citations_for, humanize_labels, mentioned_labels, render_pack, resolve_for_process
from app.db import repository
from app.db.models import ContextPackModel
from app.llm import ChatMessage, LLMClient
from app.llm.routing import llm_route_for
from app.schemas.common import ProcessSchema
from app.schemas.gap_analysis import GapFinding, GapFindingOption

from .prompts import build_system_prompt

logger = logging.getLogger("app.gap_analysis")


class GapAnalysisServiceError(Exception):
    """Raised when the LLM's reply can't be parsed into the expected
    shape -- the API layer turns this into a clean error response rather
    than a 500 crash (mirrors BlueprintServiceError/ChatServiceError)."""


class _ProposedFinding(BaseModel):
    kind: str
    question: str
    target_element_ids: list[str] = []
    options: list[GapFindingOption] = []
    citations: list[str] = []


class _ParsedFindings(BaseModel):
    findings: list[_ProposedFinding] = []


async def analyze_gaps(
    llm: LLMClient, schema: ProcessSchema, context_pack: ContextPackModel | None = None
) -> list[_ProposedFinding]:
    if not schema.elements:
        return []

    system_prompt = build_system_prompt(schema, render_pack(context_pack) if context_pack is not None else "")
    result = await llm.complete(
        [ChatMessage(role="system", content=system_prompt)],
        operation="gap_analysis",
        response_format={"type": "json_object"},
        # Same headroom as blueprint_evaluation/document_extraction --
        # Qwen3.7 Flash spends part of this budget on internal reasoning.
        max_tokens=16000,
    )

    if not result.text:
        raise GapAnalysisServiceError("LLM returned no content while analyzing gaps")

    try:
        payload: Any = json.loads(result.text)
        parsed = _ParsedFindings.model_validate(payload)
    except (json.JSONDecodeError, ValidationError) as exc:
        raise GapAnalysisServiceError(f"LLM returned invalid structured output for gap analysis: {exc}") from exc

    valid_ids = {element.id for element in schema.elements} | {flow.id for flow in schema.flows}
    findings = []
    for finding in parsed.findings:
        target_ids = [eid for eid in finding.target_element_ids if eid in valid_ids]
        if context_pack is not None:
            # Rules the model named in the question count as cited, and the
            # question itself shows codes/names rather than pack labels.
            labels = list(dict.fromkeys([*finding.citations, *mentioned_labels(context_pack, finding.question)]))
            finding = finding.model_copy(
                update={"citations": labels, "question": humanize_labels(context_pack, finding.question)}
            )
        if finding.kind == "policy":
            # Epic 28, US28.4: a policy finding may concern the process as
            # a whole (no target), but must rest on a real pack entry -- an
            # uncited "policy" finding is general knowledge, not a rule this
            # organization actually has.
            if context_pack is None or not citations_for(context_pack, finding.citations):
                continue
            findings.append(finding.model_copy(update={"target_element_ids": target_ids}))
            continue
        if not target_ids:
            # Every real finding must point at something that actually
            # exists in this diagram -- a finding with no valid target is
            # not actionable and would render with nothing to highlight.
            continue
        findings.append(_ProposedFinding(kind=finding.kind, question=finding.question, target_element_ids=target_ids, options=finding.options))

    return findings


async def run_gap_analysis(db: Session, llm: LLMClient, process_id: str) -> list[GapFinding]:
    schema = repository.get_process_schema(db, process_id)
    pack = None
    if schema is not None and schema.elements:
        pack = resolve_for_process(
            db, process_id, purpose="gap_analysis", query_text="\n".join(e.label for e in schema.elements)
        )
    with llm_route_for(db, process_id=process_id):
        proposed = await analyze_gaps(llm, schema, pack) if schema is not None else []

    # Dedup against every existing finding for this process (any status --
    # open, resolved, or dismissed): never re-create a finding the user
    # already decided on, and never duplicate one already open. v1 never
    # auto-closes an existing open finding the LLM didn't re-flag this
    # run -- ordinary LLM non-determinism auto-closing a still-real gap
    # would be worse than an occasional stale finding the user can dismiss
    # themselves (US11.3).
    existing = repository.list_gap_findings(db, process_id)
    # Policy findings also key on the rules they cite -- two different
    # rules missing from the same (or no) step are two findings.
    existing_keys = {
        (f.kind, tuple(sorted(f.target_element_ids)), tuple(sorted(c.policy_id or "" for c in f.citations)))
        for f in existing
    }

    for finding in proposed:
        citations = citations_for(pack, finding.citations)
        key = (
            finding.kind,
            tuple(sorted(finding.target_element_ids)),
            tuple(sorted(c.policy_id or "" for c in citations)),
        )
        if key in existing_keys:
            continue
        repository.add_gap_finding(
            db,
            process_id,
            kind=finding.kind,
            question=finding.question,
            target_element_ids=finding.target_element_ids,
            options=finding.options,
            citations=citations,
            context_pack_id=pack.id if pack is not None and citations else None,
        )
        existing_keys.add(key)

    repository.mark_gap_analysis_completed(db, process_id)
    return repository.list_gap_findings(db, process_id)
