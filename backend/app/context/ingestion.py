"""Epic 27, US27.6: policy-document ingestion with human confirmation.

Pipeline for one uploaded policy document, run as a background task
(same own-session pattern as app/ingestion/pipeline.py):

1. Extract blocks with Epic 1's deterministic extractors (plus plain text
   and Markdown, which policy libraries often export to).
2. Split them into clause-sized passages, embed them locally, and store
   them in `policy_passages` with the policy's scope as metadata -- the
   first data Epic 28's filtered retrieval queries.
3. Ask the LLM to propose obligations, controls, roles, approval
   authorities, and glossary terms, each citing the passages it came from.
   Proposals are stored with status "proposed"; nothing takes effect until
   a person confirms it (app/context/repository.py's confirm_entity).

Reconciliation is defensive, same principle as gap analysis: a proposal
citing no real passage is dropped, and a proposal matching an existing
confirmed entity becomes an update proposal (`supersedes_id`) -- flagged
`relaxes` when it would loosen that entity -- or is skipped if identical.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, ValidationError
from sqlalchemy.orm import Session

from app.config import Settings, get_settings
from app.db.models import ContextEntityModel, PolicyPassageModel
from app.db.session import get_session_factory
from app.ids import new_id
from app.ingestion.embeddings import embed_texts
from app.ingestion.extractors import ExtractedBlock, extract_docx, extract_pdf
from app.ingestion.pipeline import DOCX_CONTENT_TYPE, PDF_CONTENT_TYPE
from app.llm import ChatMessage, LLMClient
from app.schemas.context import AppliesTo, Citation, ContextEntityCreate

from . import repository

logger = logging.getLogger("app.context.ingestion")

TEXT_CONTENT_TYPES = {"text/plain", "text/markdown", "text/x-markdown"}
SUPPORTED_POLICY_CONTENT_TYPES = {PDF_CONTENT_TYPE, DOCX_CONTENT_TYPE, *TEXT_CONTENT_TYPES}

# Passage sizing: big enough to hold a whole clause, small enough that a
# retrieved passage is about one thing.
_PASSAGE_TARGET_CHARS = 900
_PASSAGE_MAX_CHARS = 1600
# Characters of passage text per extraction call.
_EXTRACTION_BATCH_CHARS = 24000
_TITLE_MAX_CHARS = 100
# Cosine similarity above which an extracted item is treated as the same
# rule as an existing one (all-MiniLM-L6-v2, normalized embeddings).
_SAME_RULE_SIMILARITY = 0.85


class PolicyIngestionError(Exception):
    pass


# ---- storage --------------------------------------------------------------


def _policy_path(settings: Settings, document_id: str, filename: str) -> Path:
    return settings.document_storage_root / "_organization" / f"{document_id}_{filename}"


def save_policy_file(settings: Settings, document_id: str, filename: str, data: bytes) -> None:
    path = _policy_path(settings, document_id, filename)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def read_policy_file(settings: Settings, document_id: str, filename: str) -> bytes:
    return _policy_path(settings, document_id, filename).read_bytes()


def delete_policy_file(settings: Settings, document_id: str, filename: str) -> None:
    _policy_path(settings, document_id, filename).unlink(missing_ok=True)


def content_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


# ---- passages -------------------------------------------------------------


@dataclass
class Passage:
    clause_ref: str
    text: str


def extract_text_blocks(data: bytes) -> list[ExtractedBlock]:
    text = data.decode("utf-8", errors="replace")
    blocks = []
    for index, chunk in enumerate(re.split(r"\n\s*\n", text), start=1):
        chunk = chunk.strip()
        if chunk:
            blocks.append(ExtractedBlock(kind="text", location=f"paragraph {index}", content=chunk))
    return blocks


def extract_blocks(content_type: str, data: bytes) -> list[ExtractedBlock]:
    if content_type == PDF_CONTENT_TYPE:
        return extract_pdf(data)
    if content_type == DOCX_CONTENT_TYPE:
        return extract_docx(data)
    if content_type in TEXT_CONTENT_TYPES:
        return extract_text_blocks(data)
    raise PolicyIngestionError(f"No extractor for content type '{content_type}'")


_HEADING = re.compile(r"^(#{1,6}\s|\d+(\.\d+)*[.)]?\s+[A-Z]|section\s+\d|article\s+\d|clause\s+\d)", re.IGNORECASE)


def split_into_passages(blocks: list[ExtractedBlock]) -> list[Passage]:
    """Greedy merge of paragraphs into ~clause-sized passages. A heading
    starts a new passage, so a clause doesn't get glued onto the end of
    the previous one. A PDF page (one block) is first split on blank
    lines; anything still over the max is hard-wrapped at sentence ends."""
    pieces: list[tuple[str, str]] = []
    for block in blocks:
        parts = [p.strip() for p in re.split(r"\n\s*\n", block.content) if p.strip()] or [block.content.strip()]
        for part in parts:
            while len(part) > _PASSAGE_MAX_CHARS:
                cut = part.rfind(". ", 0, _PASSAGE_MAX_CHARS)
                cut = cut + 1 if cut > _PASSAGE_TARGET_CHARS // 2 else _PASSAGE_MAX_CHARS
                pieces.append((block.location, part[:cut].strip()))
                part = part[cut:].strip()
            if part:
                pieces.append((block.location, part))

    passages: list[Passage] = []
    current: list[str] = []
    first_location = last_location = ""

    def flush() -> None:
        ref = first_location if first_location == last_location else f"{first_location} to {last_location}"
        # A passage that opens with a heading is cited by that heading
        # ("3. Sanctions screening"), which is what a reader of the policy
        # recognizes -- the extractor's paragraph number goes in brackets.
        first_line = current[0].splitlines()[0].strip().lstrip("#").strip()
        if _HEADING.match(current[0]) and len(first_line) <= 80:
            ref = f"{first_line} ({ref})"
        passages.append(Passage(clause_ref=ref, text="\n".join(current)))

    for location, text in pieces:
        size = sum(len(t) for t in current)
        # A lone short line (a document title) is not a clause on its own --
        # observed live as a retrieved passage reading only "Meridian Group
        # -- Customer Onboarding Policy". It joins whatever follows.
        lone_title = len(current) == 1 and size < _TITLE_MAX_CHARS and not _HEADING.match(current[0])
        if current and not lone_title and (_HEADING.match(text) or size + len(text) > _PASSAGE_TARGET_CHARS):
            flush()
            current = []
        if not current:
            first_location = location
        current.append(text)
        last_location = location
    if current:
        flush()
    return passages


def store_passages(
    session: Session,
    *,
    document_id: str | None,
    policy_entity_id: str,
    policy_version: int,
    passages: list[Passage],
    applies_to: AppliesTo,
) -> list[PolicyPassageModel]:
    """Older passages of the same policy are kept (pinned packs cite them)
    but marked not current, so retrieval only sees the latest text."""
    for old in session.query(PolicyPassageModel).filter(PolicyPassageModel.policy_entity_id == policy_entity_id):
        old.current = False
    vectors = embed_texts([p.text for p in passages])
    rows = []
    for ordinal, (passage, vector) in enumerate(zip(passages, vectors)):
        row = PolicyPassageModel(
            id=new_id("psg"),
            document_id=document_id,
            policy_entity_id=policy_entity_id,
            policy_version=policy_version,
            ordinal=ordinal,
            clause_ref=passage.clause_ref,
            text=passage.text,
            embedding=vector,
            applies_to=applies_to.model_dump(),
            current=True,
        )
        session.add(row)
        rows.append(row)
    session.flush()
    return rows


# ---- LLM extraction -------------------------------------------------------


class _ProposedItem(BaseModel):
    citations: list[str] = []
    confidence: float = Field(default=0.5, ge=0, le=1)


class _ProposedObligation(_ProposedItem):
    code: str | None = None
    title: str
    text: str = ""


class _ProposedControl(_ProposedItem):
    code: str | None = None
    title: str
    description: str = ""
    control_type: str = "other"
    requires_human_review: bool = False
    required_guardrails: list[str] = []
    review_role: str | None = None
    obligation_code: str | None = None
    testable: bool = True


class _ProposedRole(_ProposedItem):
    name: str
    description: str = ""


class _ProposedAuthority(_ProposedItem):
    role: str
    decision_type: str
    limit_amount: float | None = None
    currency: str = ""


class _ProposedTerm(_ProposedItem):
    term: str
    definition: str
    synonyms: list[str] = []


class _ExtractionResult(BaseModel):
    obligations: list[_ProposedObligation] = []
    controls: list[_ProposedControl] = []
    roles: list[_ProposedRole] = []
    approval_authorities: list[_ProposedAuthority] = []
    glossary_terms: list[_ProposedTerm] = []


_CONTROL_TYPES = {"human_review", "logging", "segregation_of_duties", "data_protection", "notification", "other"}


def build_extraction_prompt(policy_title: str, numbered_passages: list[tuple[str, str]]) -> str:
    passages_text = "\n\n".join(f"[{label}]\n{text}" for label, text in numbered_passages)
    return f"""You extract an organization's binding rules from one of its policy documents, for a compliance team to review.

Policy: {policy_title}

Read the numbered passages and return a single JSON object with these keys (each a list, possibly empty):

- "obligations": things the organization MUST or MUST NOT do. Fields: "code" (the policy's own reference if it has one, else null), "title" (one line), "text" (the requirement in plain language).
- "controls": concrete checks or steps that implement an obligation. Fields: "code" (or null), "title", "description", "control_type" (one of: human_review, logging, segregation_of_duties, data_protection, notification, other), "requires_human_review" (true only if the passage requires a person to review, approve, or decide), "required_guardrails" (short snake_case names such as decision_logging, explanation, escalate_on_potential_match), "review_role" (the role that must review, or null), "obligation_code" (the obligation it implements, or null), "testable" (false for purely organizational controls a single case can't exercise, such as annual training or a yearly policy review; otherwise true).
- "roles": named roles with responsibilities. Fields: "name", "description".
- "approval_authorities": who may approve what, up to which limit. Fields: "role", "decision_type", "limit_amount" (a number, or null for no limit), "currency".
- "glossary_terms": terms the policy defines. Fields: "term", "definition", "synonyms".

Every item MUST include "citations": the passage labels (e.g. ["P3"]) it is taken from, and "confidence": 0 to 1, how clearly the passage states it.

Rules:
- Extract only what the passages actually say. Do not add rules from general knowledge.
- Prefer missing an item to inventing one, EXCEPT for human-review requirements: if a passage says a person must review, approve, or sign off, always extract that control, even with lower confidence.
- Quote limits and thresholds exactly as written.

Passages:

{passages_text}
"""


def _batches(passages: list[PolicyPassageModel]) -> list[list[PolicyPassageModel]]:
    batches: list[list[PolicyPassageModel]] = []
    current: list[PolicyPassageModel] = []
    size = 0
    for passage in passages:
        if current and size + len(passage.text) > _EXTRACTION_BATCH_CHARS:
            batches.append(current)
            current, size = [], 0
        current.append(passage)
        size += len(passage.text)
    if current:
        batches.append(current)
    return batches


async def extract_proposals(llm: LLMClient, policy_title: str, passages: list[PolicyPassageModel]) -> list[tuple[_ExtractionResult, dict[str, PolicyPassageModel]]]:
    results = []
    for batch in _batches(passages):
        labels = {f"P{index}": passage for index, passage in enumerate(batch, start=1)}
        prompt = build_extraction_prompt(policy_title, [(label, p.text) for label, p in labels.items()])
        reply = await llm.complete(
            [ChatMessage(role="system", content=prompt)],
            operation="policy_extraction",
            response_format={"type": "json_object"},
            max_tokens=16000,
        )
        if not reply.text:
            raise PolicyIngestionError("LLM returned no content while extracting policy rules")
        try:
            parsed = _ExtractionResult.model_validate(json.loads(reply.text))
        except (json.JSONDecodeError, ValidationError) as exc:
            raise PolicyIngestionError(f"LLM returned invalid structured output for policy extraction: {exc}") from exc
        results.append((parsed, labels))
    return results


def _citations(item: _ProposedItem, labels: dict[str, PolicyPassageModel], policy_entity_id: str) -> list[Citation]:
    citations = []
    for label in item.citations:
        passage = labels.get(label.strip().strip("[]"))
        if passage is not None:
            citations.append(
                Citation(
                    passage_id=passage.id,
                    policy_id=policy_entity_id,
                    clause_ref=passage.clause_ref,
                    quote=passage.text[:280],
                )
            )
    return citations


def _is_relaxation(kind: str, existing: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Notes: anything that would loosen an existing rule needs explicit
    confirmation. Loosening = a control no longer requiring human review
    or dropping guardrails, or an approval limit going up (or becoming
    unlimited)."""
    if kind == "control":
        if existing.get("requires_human_review") and not proposed.get("requires_human_review"):
            return True
        return bool(set(existing.get("required_guardrails", [])) - set(proposed.get("required_guardrails", [])))
    if kind == "approval_authority":
        old, new = existing.get("limit_amount"), proposed.get("limit_amount")
        if old is None:
            return False
        return new is None or new > old
    return False


_PROSE_FIELDS = {"text", "description", "definition", "summary"}
_REWORDING_SIMILARITY = 0.9


def _only_rewording(kind: str, existing: dict[str, Any], proposed: dict[str, Any]) -> bool:
    """Live finding (2026-10-11): re-extracting an unchanged policy yielded
    "update" proposals that merely reworded confirmed rules. An update is
    material only if a structured field changes (review requirement,
    guardrails, role, limit, ...) or its prose means something different."""
    existing = repository.validate_data(kind, existing)
    proposed = repository.validate_data(kind, proposed)
    for key in set(existing) | set(proposed):
        if key in _PROSE_FIELDS:
            continue
        old, new = existing.get(key), proposed.get(key)
        if isinstance(old, list) and isinstance(new, list):
            if {str(v).lower() for v in old} != {str(v).lower() for v in new}:
                return False
        elif (old.lower() if isinstance(old, str) else old) != (new.lower() if isinstance(new, str) else new):
            return False
    pairs = [(existing.get(k) or "", proposed.get(k) or "") for k in _PROSE_FIELDS if existing.get(k) or proposed.get(k)]
    if not pairs:
        return True
    olds, news = embed_texts([a for a, _ in pairs]), embed_texts([b for _, b in pairs])
    return all(sum(x * y for x, y in zip(a, b)) >= _REWORDING_SIMILARITY for a, b in zip(olds, news))


def _propose(
    session: Session,
    *,
    kind: str,
    name: str,
    code: str | None,
    data: dict[str, Any],
    applies_to: AppliesTo,
    parent_id: str | None,
    citations: list[Citation],
    confidence: float,
    source: str,
    existing,
    created_by: str | None,
) -> str | None:
    """Returns the new proposal's id, or None when it would change
    nothing."""
    supersedes_id = None
    relaxes = False
    if existing is not None:
        if existing.name == name and (existing.data or {}) == repository.validate_data(kind, data):
            return None
        if _only_rewording(kind, existing.data or {}, data):
            return None
        supersedes_id = existing.id
        relaxes = _is_relaxation(kind, existing.data or {}, data)
    proposal = repository.create_entity(
        session,
        ContextEntityCreate(kind=kind, name=name, code=code, data=data, applies_to=applies_to, parent_id=parent_id),  # type: ignore[arg-type]
        status="proposed",
        source=source,
        confidence=confidence,
        citations=citations,
        supersedes_id=supersedes_id,
        relaxes=relaxes,
        created_by=created_by,
    )
    return proposal.id


class _RuleMatcher:
    """Live finding (2026-10-11): re-extracting a policy produced "MLRO
    Review of Potential Sanctions Match", "...Matches", and "MLRO Review of
    Sanctions Matches" as three separate rules -- extracted names vary run
    to run, so exact-name matching can't dedup them. This finds the existing
    entity (confirmed or still awaiting review) an item most likely
    restates: exact code/name first, then embedding similarity over name
    plus text. New proposals join the index, so near-duplicates within one
    extraction are caught too."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self._index: dict[str, list[tuple[ContextEntityModel, list[float]]]] = {}

    @staticmethod
    def _text(kind: str, name: str, data: dict[str, Any]) -> str:
        if kind == "approval_authority":
            return f"{data.get('role_name', '')} approves {data.get('decision_type', '')}"
        body = data.get("text") or data.get("description") or data.get("definition") or ""
        return f"{name}. {body}"

    def _entries(self, kind: str) -> list[tuple[ContextEntityModel, list[float]]]:
        if kind not in self._index:
            entities = repository.list_entities(self.session, kind=kind, status=["confirmed", "proposed"])
            vectors = embed_texts([self._text(kind, e.name, e.data or {}) for e in entities])
            self._index[kind] = list(zip(entities, vectors))
        return self._index[kind]

    def find(self, kind: str, *, code: str | None, name: str, data: dict[str, Any]) -> ContextEntityModel | None:
        exact = repository.find_entity(self.session, kind, code=code, name=name)
        if exact is not None:
            return exact
        entries = self._entries(kind)
        if not entries:
            return None
        vector = embed_texts([self._text(kind, name, data)])[0]
        best, best_score = None, _SAME_RULE_SIMILARITY
        for entity, other in entries:
            score = sum(a * b for a, b in zip(vector, other))
            if score >= best_score:
                best, best_score = entity, score
        return best

    def add(self, entity_id: str | None) -> None:
        if entity_id is None:
            return
        entity = self.session.get(ContextEntityModel, entity_id)
        if entity is not None and entity.kind in self._index:
            self._index[entity.kind].append((entity, embed_texts([self._text(entity.kind, entity.name, entity.data or {})])[0]))


def reconcile_proposals(
    session: Session,
    extraction: _ExtractionResult,
    labels: dict[str, PolicyPassageModel],
    *,
    policy_entity_id: str,
    applies_to: AppliesTo,
    source: str,
    created_by: str | None,
) -> int:
    count = 0
    obligation_ids: dict[str, str] = {}
    matcher = _RuleMatcher(session)

    for item in extraction.obligations:
        citations = _citations(item, labels, policy_entity_id)
        if not citations:
            continue
        existing = matcher.find("obligation", code=item.code, name=item.title, data={"text": item.text})
        if existing is not None and existing.status == "proposed":
            continue  # already awaiting review
        proposal_id = _propose(
            session, kind="obligation", name=item.title, code=item.code, data={"text": item.text},
            applies_to=applies_to, parent_id=policy_entity_id, citations=citations, confidence=item.confidence,
            source=source, existing=existing, created_by=created_by,
        )
        count += proposal_id is not None
        matcher.add(proposal_id)
        # Controls hang off the live obligation when there is one (a
        # superseding proposal is deleted on confirm), else off the new
        # proposal, which keeps its id when confirmed.
        target = existing.id if existing is not None else proposal_id
        if item.code and target:
            obligation_ids[item.code.lower()] = target

    for item in extraction.controls:
        citations = _citations(item, labels, policy_entity_id)
        if not citations:
            continue
        data = {
            "description": item.description,
            "control_type": item.control_type if item.control_type in _CONTROL_TYPES else "other",
            "requires_human_review": item.requires_human_review,
            "required_guardrails": item.required_guardrails,
            "review_role": item.review_role,
            "testable": item.testable,
        }
        parent = obligation_ids.get((item.obligation_code or "").lower(), policy_entity_id)
        existing = matcher.find("control", code=item.code, name=item.title, data=data)
        if existing is not None and existing.status == "proposed":
            continue
        proposal_id = _propose(
            session, kind="control", name=item.title, code=item.code, data=data, applies_to=applies_to,
            parent_id=parent, citations=citations, confidence=item.confidence, source=source,
            existing=existing, created_by=created_by,
        )
        count += proposal_id is not None
        matcher.add(proposal_id)

    for item in extraction.roles:
        citations = _citations(item, labels, policy_entity_id)
        if not citations:
            continue
        existing = repository.find_entity(session, "role", name=item.name)
        if existing is not None:
            continue  # a role's name is its identity; a different description isn't worth a review item
        if _propose(
            session, kind="role", name=item.name, code=None, data={"description": item.description},
            applies_to=AppliesTo(), parent_id=None, citations=citations, confidence=item.confidence,
            source=source, existing=None, created_by=created_by,
        ):
            count += 1

    for item in extraction.approval_authorities:
        citations = _citations(item, labels, policy_entity_id)
        if not citations:
            continue
        role = repository.find_entity(session, "role", name=item.role)
        data = {
            "role_id": role.id if role else None,
            "role_name": item.role,
            "decision_type": item.decision_type,
            "limit_amount": item.limit_amount,
            "currency": item.currency,
        }
        existing = matcher.find("approval_authority", code=None, name=f"{item.role}: {item.decision_type}", data=data)
        if existing is not None and existing.status == "proposed":
            continue
        if existing is not None and (existing.data or {}).get("role_name", "").lower() != item.role.lower():
            existing = None  # similar wording, different role: a separate authority
        proposal_id = _propose(
            session, kind="approval_authority", name=f"{item.role}: {item.decision_type}", code=None, data=data,
            applies_to=applies_to, parent_id=role.id if role else None, citations=citations,
            confidence=item.confidence, source=source, existing=existing, created_by=created_by,
        )
        count += proposal_id is not None
        matcher.add(proposal_id)

    for item in extraction.glossary_terms:
        citations = _citations(item, labels, policy_entity_id)
        if not citations:
            continue
        existing = repository.find_entity(session, "glossary_term", name=item.term)
        if existing is None and any(
            e.name.lower() == item.term.lower() for e in repository.list_entities(session, kind="glossary_term", status="proposed")
        ):
            continue
        if _propose(
            session, kind="glossary_term", name=item.term, code=item.term.lower(),
            data={"definition": item.definition, "synonyms": item.synonyms}, applies_to=applies_to,
            parent_id=None, citations=citations, confidence=item.confidence, source=source,
            existing=existing, created_by=created_by,
        ):
            count += 1

    return count


# ---- background task ------------------------------------------------------


async def process_policy_document(document_id: str, llm: LLMClient, settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    session = get_session_factory()()
    try:
        document = repository.get_context_document_model(session, document_id)
        document.status = "processing"
        session.commit()

        policy = repository.get_entity_model(session, document.policy_entity_id) if document.policy_entity_id else None
        if policy is None:
            raise PolicyIngestionError("Policy document has no policy entity")
        applies_to = AppliesTo.model_validate(policy.applies_to or {})

        data = read_policy_file(settings, document_id, document.filename)
        blocks = extract_blocks(document.content_type, data)
        passages = split_into_passages(blocks)
        if not passages:
            raise PolicyIngestionError("No text could be extracted from this document")
        rows = store_passages(
            session,
            document_id=document_id,
            policy_entity_id=policy.id,
            policy_version=policy.version,
            passages=passages,
            applies_to=applies_to,
        )
        session.commit()

        count = 0
        from app.llm.routing import llm_route_for

        unit = policy.owner_business_unit_id or next(iter(applies_to.business_units), None)
        with llm_route_for(session, business_unit_id=unit):
            extractions = await extract_proposals(llm, policy.name, rows)
        for extraction, labels in extractions:
            count += reconcile_proposals(
                session,
                extraction,
                labels,
                policy_entity_id=policy.id,
                applies_to=applies_to,
                source=f"document:{document_id}",
                created_by=document.created_by,
            )
        document.proposal_count = count
        document.status = "done"
        session.commit()
    except Exception as exc:
        session.rollback()
        logger.warning("policy_document_failed", extra={"document_id": document_id, "error": str(exc)})
        try:
            document = repository.get_context_document_model(session, document_id)
            document.status = "failed"
            document.error_message = str(exc)
            session.commit()
        except Exception:
            session.rollback()
    finally:
        session.close()
