"""Epic 28, US28.1: the context resolver. Call sites never query the
context store directly -- they ask for a Context Pack for a scope and a
purpose, and get back a bounded, versioned, citable slice.

Three steps (decided in planning/enterprise-context-strategy.md):

1. Deterministic applicability -- which confirmed entities apply to the
   scope (app/context/applicability.py).
2. Semantic retrieval -- within the policies that apply, the passages most
   relevant to this call's text (pgvector cosine distance, restricted to
   current passages of applicable policies).
3. Token budget per call site -- structured facts (human-review controls,
   approval limits, roles) rank above prose; whatever doesn't fit is
   dropped and counted in `truncated`.

Packs are immutable and content-addressed: identical content resolves to
the same pack row, so pinning a pack id pins exact entity versions.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import ContextEntityModel, ContextPackModel, PolicyPassageModel
from app.ids import new_id
from app.ingestion.embeddings import embed_texts
from app.schemas.context import (
    AppliesTo,
    Citation,
    ContextPack,
    ContextPackEntry,
    ContextPackPassage,
    ProcessScope,
    StaleEntry,
)
from app.store import NotFoundError

from . import repository
from .applicability import applies

# Tokens per call site (US28.1 / Notes: budgets are mandatory). Rough
# 4-characters-per-token estimate -- good enough for budgeting, not billing.
PURPOSE_BUDGETS: dict[str, int] = {
    "gap_analysis": 2500,
    "blueprint_evaluation": 3500,
    "agent_generation": 2000,
    "chat_edit": 800,
    "extraction": 800,
    "test_builder": 2000,
    "inspector": 8000,
}
DEFAULT_BUDGET = 2000
_MAX_PASSAGES = 8

# Lower rank = included first.
_KIND_RANK = {
    "control": 1,
    "approval_authority": 2,
    "role": 3,
    "obligation": 4,
    "data_class": 5,
    "application": 6,
    "regulation": 7,
    "glossary_term": 8,
}
_SKIPPED_KINDS = {"business_unit", "jurisdiction", "policy"}


def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def entity_text(entity: ContextEntityModel) -> str:
    """One self-contained line per entity -- what the model actually sees."""
    d = entity.data or {}
    head = f"{entity.code}: {entity.name}" if entity.code else entity.name
    if entity.kind == "control":
        parts = [f"Control {head}.", d.get("description", "")]
        if d.get("requires_human_review"):
            parts.append(f"Requires human review{' by ' + d['review_role'] if d.get('review_role') else ''}.")
        if d.get("required_guardrails"):
            parts.append("Required guardrails: " + ", ".join(d["required_guardrails"]) + ".")
        return " ".join(p for p in parts if p)
    if entity.kind == "approval_authority":
        limit = "no limit" if d.get("limit_amount") is None else f"up to {d['limit_amount']:g} {d.get('currency', '')}".strip()
        return f"Approval authority: {d.get('role_name') or 'role'} may approve {d.get('decision_type')} ({limit})."
    if entity.kind == "obligation":
        return f"Obligation {head}. {d.get('text', '')}".strip()
    if entity.kind == "role":
        return f"Role {entity.name}. {d.get('description', '')}".strip()
    if entity.kind == "glossary_term":
        synonyms = f" Also called: {', '.join(d['synonyms'])}." if d.get("synonyms") else ""
        return f"Term '{entity.name}': {d.get('definition', '')}{synonyms}"
    if entity.kind == "application":
        aliases = f" Also known as: {', '.join(d['aliases'])}." if d.get("aliases") else ""
        classes = f" Holds data classes: {', '.join(d['data_classes'])}." if d.get("data_classes") else ""
        return f"Application {entity.name}.{aliases}{classes} {d.get('description', '')}".strip()
    if entity.kind == "data_class":
        return f"Data class {entity.name} (sensitivity {d.get('sensitivity', 1)}). {d.get('description', '')}".strip()
    if entity.kind == "regulation":
        return f"Regulation {head}. {d.get('summary', '')}".strip()
    return head


def _priority(entity: ContextEntityModel) -> tuple[int, int, str]:
    human_review_first = 0 if entity.kind == "control" and (entity.data or {}).get("requires_human_review") else 1
    return (_KIND_RANK.get(entity.kind, 9), human_review_first, entity.name.lower())


# Vocabulary-oriented call sites (US28.7) see only names and terms, and
# only the ones the text actually mentions.
VOCABULARY_KINDS = ["glossary_term", "application"]
_VOCABULARY_PURPOSES = {"chat_edit", "extraction"}


def mention_hits(entities: list[ContextEntityModel], text: str, kinds: set[str]) -> set[str]:
    """Ids of `kinds` entities whose name, synonym, or alias the text uses."""
    lowered = text.lower()
    hits = set()
    for entity in entities:
        if entity.kind not in kinds:
            continue
        data = entity.data or {}
        words = [entity.name, *(data.get("synonyms") or []), *(data.get("aliases") or [])]
        if any(re.search(rf"\b{re.escape(w.lower())}\b", lowered) for w in words if w.strip()):
            hits.add(entity.id)
    return hits


def retrieve_passages(
    session: Session, scope: ProcessScope, query_text: str, *, limit: int = _MAX_PASSAGES
) -> list[tuple[PolicyPassageModel, float]]:
    if not query_text.strip():
        return []
    parent_of = repository.jurisdiction_parent_lookup(session)
    policy_ids = [
        p.id
        for p in repository.list_entities(session, kind="policy", status="confirmed")
        if applies(AppliesTo.model_validate(p.applies_to or {}), scope, parent_of)
    ]
    if not policy_ids:
        return []
    vector = embed_texts([query_text[:4000]])[0]
    distance = PolicyPassageModel.embedding.cosine_distance(vector)
    rows = session.execute(
        select(PolicyPassageModel, distance.label("distance"))
        .where(PolicyPassageModel.current.is_(True), PolicyPassageModel.policy_entity_id.in_(policy_ids))
        .order_by(distance)
        .limit(limit)
    ).all()
    return [(row[0], 1.0 - float(row[1])) for row in rows]


def resolve_pack(
    session: Session,
    scope: ProcessScope,
    *,
    purpose: str,
    query_text: str = "",
    systems: list[str] | None = None,
    process_id: str | None = None,
    budget: int | None = None,
    audit_llm: bool = True,
) -> ContextPackModel:
    pack = _resolve_pack(
        session, scope, purpose=purpose, query_text=query_text, systems=systems, process_id=process_id, budget=budget
    )
    if audit_llm and not is_empty(pack):
        # Epic 31, US31.10: every pack handed to an LLM call site is a read.
        from .audit import record

        record(
            session,
            actor_type="llm",
            actor=f"llm:{purpose}",
            action="llm_context",
            process_id=process_id,
            pack_id=pack.id,
            entity_ids=[e["entity_id"] for e in pack.entries],
            business_unit_id=scope.business_unit_id,
        )
    return pack


def _resolve_pack(
    session: Session,
    scope: ProcessScope,
    *,
    purpose: str,
    query_text: str = "",
    systems: list[str] | None = None,
    process_id: str | None = None,
    budget: int | None = None,
) -> ContextPackModel:
    budget = budget or PURPOSE_BUDGETS.get(purpose, DEFAULT_BUDGET)
    vocabulary_only = purpose in _VOCABULARY_PURPOSES
    applicable = [
        e
        for e in repository.applicable_entities(session, scope, systems=systems)
        if e.kind not in _SKIPPED_KINDS and (not vocabulary_only or e.kind in VOCABULARY_KINDS)
    ]
    gated = {"glossary_term", "application"} if vocabulary_only else {"glossary_term"}
    hits = mention_hits(applicable, query_text, gated)
    candidates = sorted((e for e in applicable if e.kind not in gated or e.id in hits), key=_priority)

    used = 0
    truncated = 0
    entries: list[dict[str, Any]] = []
    for entity in candidates:
        text = entity_text(entity)
        cost = estimate_tokens(text)
        if used + cost > budget:
            truncated += 1
            continue
        used += cost
        entries.append(
            {
                "entity_id": entity.id,
                "kind": entity.kind,
                "code": entity.code,
                "name": entity.name,
                "version": entity.version,
                "text": text,
            }
        )

    passages: list[dict[str, Any]] = []
    for passage, score in [] if vocabulary_only else retrieve_passages(session, scope, query_text):
        cost = estimate_tokens(passage.text)
        if used + cost > budget:
            truncated += 1
            continue
        used += cost
        passages.append(
            {
                "passage_id": passage.id,
                "policy_entity_id": passage.policy_entity_id,
                "policy_version": passage.policy_version,
                "clause_ref": passage.clause_ref,
                "text": passage.text,
                "score": round(score, 4),
            }
        )

    for index, entry in enumerate(entries, start=1):
        entry["label"] = f"C{index}"
    for index, passage in enumerate(passages, start=1):
        passage["label"] = f"P{index}"

    scope_dump = scope.model_dump()
    content = {
        "purpose": purpose,
        "scope": scope_dump,
        "process_id": process_id,
        "entries": [(e["entity_id"], e["version"]) for e in entries],
        "passages": [p["passage_id"] for p in passages],
        "budget": budget,
    }
    content_hash = hashlib.sha256(json.dumps(content, sort_keys=True).encode()).hexdigest()
    existing = session.scalar(select(ContextPackModel).where(ContextPackModel.content_hash == content_hash))
    if existing is not None:
        return existing
    pack = ContextPackModel(
        id=new_id("ctxpack"),
        content_hash=content_hash,
        purpose=purpose,
        scope=scope_dump,
        process_id=process_id,
        entries=entries,
        passages=passages,
        token_estimate=used,
        budget=budget,
        truncated=truncated,
    )
    session.add(pack)
    session.flush()
    return pack


def resolve_for_process(
    session: Session,
    process_id: str,
    *,
    purpose: str,
    query_text: str = "",
    systems: list[str] | None = None,
    audit_llm: bool = True,
) -> ContextPackModel:
    scope = repository.get_process_scope(session, process_id)
    return resolve_pack(
        session, scope, purpose=purpose, query_text=query_text, systems=systems, process_id=process_id, audit_llm=audit_llm
    )


def is_empty(pack: ContextPackModel) -> bool:
    return not pack.entries and not pack.passages


def render_pack(pack: ContextPackModel) -> str:
    """The prompt section. Labels are what the model cites back (US28.3);
    the closing line keeps the legal positioning explicit."""
    if is_empty(pack):
        return ""
    if pack.purpose in _VOCABULARY_PURPOSES:
        lines = ["## Organization vocabulary", "The organization's own names for systems and terms mentioned here.", ""]
    else:
        lines = [
            "## Organization context",
            "Rules, roles, and policy text that apply to this process. Each item has a label in brackets.",
            "Where an item affects your answer, cite its label in the citation field asked for. In any text a",
            "person will read (questions, rationales), refer to an item by its code or name -- never by its",
            "bracketed label, which people cannot see. Do not invent rules that are not listed here.",
            "",
        ]
    for entry in pack.entries:
        lines.append(f"[{entry['label']}] {entry['text']}")
    if pack.passages:
        lines.append("")
        lines.append("Relevant policy passages:")
        for passage in pack.passages:
            lines.append(f"[{passage['label']}] ({passage['clause_ref']}) {passage['text']}")
    return "\n".join(lines)


_LABEL_TOKEN = re.compile(r"\[?\b([CP]\d{1,3})\b\]?")


def _label_names(pack: ContextPackModel) -> dict[str, str]:
    names = {e["label"]: e.get("code") or e["name"] for e in pack.entries}
    names.update({p["label"]: p["clause_ref"] for p in pack.passages})
    return names


def mentioned_labels(pack: ContextPackModel | None, text: str) -> list[str]:
    """Pack labels a model wrote into prose despite being told not to --
    observed live (2026-10-11): gap questions said "as required by C3,
    C6, and C29"."""
    if pack is None:
        return []
    names = _label_names(pack)
    found: list[str] = []
    for match in _LABEL_TOKEN.finditer(text):
        label = match.group(1)
        if label in names and label not in found:
            found.append(label)
    return found


def humanize_labels(pack: ContextPackModel | None, text: str) -> str:
    """Replaces pack labels in text meant for people with the item's code
    or name. Tokens that look like labels but aren't in the pack are left
    alone (they may be legitimate text, e.g. a "P1" priority)."""
    if pack is None or not text:
        return text
    names = _label_names(pack)

    def replace(match: re.Match[str]) -> str:
        return names.get(match.group(1), match.group(0))

    return _LABEL_TOKEN.sub(replace, text)


def citations_for(pack: ContextPackModel | None, labels: list[str]) -> list[Citation]:
    """Maps labels the model cited back to Citations. Unknown labels are
    dropped (same never-trust-the-LLM's-ids rule as everywhere else)."""
    if pack is None:
        return []
    by_label: dict[str, Citation] = {}
    for entry in pack.entries:
        by_label[entry["label"]] = Citation(
            passage_id=None,
            policy_id=entry["entity_id"],
            clause_ref=entry.get("code") or entry["name"],
            quote=entry["text"][:280],
        )
    for passage in pack.passages:
        by_label[passage["label"]] = Citation(
            passage_id=passage["passage_id"],
            policy_id=passage.get("policy_entity_id"),
            clause_ref=passage["clause_ref"],
            quote=passage["text"][:280],
        )
    seen = set()
    citations = []
    for label in labels:
        label = label.strip().strip("[]")
        if label in by_label and label not in seen:
            seen.add(label)
            citations.append(by_label[label])
    return citations


def cited_entities(session: Session, citations: list[Citation]) -> list[ContextEntityModel]:
    """The context entities a set of citations points at (entry citations
    carry the entity id in `policy_id`; passage citations point at their
    policy)."""
    ids = {c.policy_id for c in citations if c.policy_id}
    return [e for e in (session.get(ContextEntityModel, i) for i in ids) if e is not None]


def get_pack_model(session: Session, pack_id: str) -> ContextPackModel:
    pack = session.get(ContextPackModel, pack_id)
    if pack is None:
        raise NotFoundError("context pack", pack_id)
    return pack


def stale_entries(session: Session, pack: ContextPackModel) -> list[StaleEntry]:
    """US28.8: computed on read, the same "derive, don't store" choice as
    Epic 12's artifact staleness -- a pinned entry is stale when its entity
    has a newer version or is no longer confirmed."""
    stale = []
    for entry in pack.entries:
        entity = session.get(ContextEntityModel, entry["entity_id"])
        if entity is None or entity.version != entry["version"] or entity.status != "confirmed":
            stale.append(
                StaleEntry(
                    entity_id=entry["entity_id"],
                    kind=entry["kind"],
                    name=entry["name"],
                    pinned_version=entry["version"],
                    current_version=entity.version if entity else None,
                    current_status=entity.status if entity else None,
                )
            )
    for passage in pack.passages:
        policy_id = passage.get("policy_entity_id")
        entity = session.get(ContextEntityModel, policy_id) if policy_id else None
        if entity is not None and entity.version != passage["policy_version"]:
            stale.append(
                StaleEntry(
                    entity_id=entity.id,
                    kind="policy",
                    name=entity.name,
                    pinned_version=passage["policy_version"],
                    current_version=entity.version,
                    current_status=entity.status,
                )
            )
    return stale


def is_pack_stale(session: Session, pack_id: str | None) -> bool:
    if not pack_id:
        return False
    pack = session.get(ContextPackModel, pack_id)
    return pack is not None and bool(stale_entries(session, pack))


def to_pack(session: Session, pack: ContextPackModel) -> ContextPack:
    return ContextPack(
        id=pack.id,
        purpose=pack.purpose,
        scope=ProcessScope.model_validate(pack.scope or {}),
        process_id=pack.process_id,
        entries=[ContextPackEntry.model_validate(e) for e in pack.entries],
        passages=[ContextPackPassage.model_validate(p) for p in pack.passages],
        token_estimate=pack.token_estimate,
        budget=pack.budget,
        truncated=pack.truncated,
        created_at=pack.created_at,
        stale_entries=stale_entries(session, pack),
        rendered=render_pack(pack),
    )


def packs_containing(session: Session, entity_id: str) -> list[ContextPackModel]:
    """Packs pinning `entity_id` as an entry or via one of its passages.
    Scans in Python: packs are few per process and JSON containment isn't
    indexed -- fine at single-organization scale."""
    result = []
    for pack in session.scalars(select(ContextPackModel)):
        if any(e["entity_id"] == entity_id for e in pack.entries) or any(
            p.get("policy_entity_id") == entity_id for p in pack.passages
        ):
            result.append(pack)
    return result
