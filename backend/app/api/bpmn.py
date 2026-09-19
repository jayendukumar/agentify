from fastapi import APIRouter, HTTPException, status

from app.bpmn.builder import build_bpmn_xml
from app.bpmn.validation import validate_bpmn
from app.db import repository
from app.schemas.bpmn import BPMNDocument, BPMNGenerateRequest, BPMNUpdateRequest

from .deps import CurrentUserDep, DbDep, EditorDep, LLMDep

router = APIRouter(prefix="/api/processes/{process_id}/bpmn", tags=["bpmn"])


def _to_schema(draft) -> BPMNDocument:
    return BPMNDocument(
        process_id=draft.process_id,
        xml=draft.xml,
        low_confidence_element_ids=list(draft.low_confidence_element_ids or []),
        # Computed fresh, not stored -- cheap pure function, and this way it
        # never goes stale if app/bpmn/validation.py's checks change later.
        validation_issues=validate_bpmn(draft.xml),
        generated_at=draft.generated_at,
    )


@router.post("/generate", response_model=BPMNDocument)
async def generate_bpmn(
    process_id: str, body: BPMNGenerateRequest, db: DbDep, llm: LLMDep, user: EditorDep
) -> BPMNDocument:
    del user
    repository.get_process(db, process_id)  # 404s if missing

    # US3.7: this always regenerates from the process's *current*, fully
    # merged schema -- body.document_ids isn't applied as a filter. Once
    # documents are merged (Epic 1, US1.8) their individual contributions
    # aren't cleanly separable, so "regenerate from a subset of documents"
    # isn't a coherent operation yet; re-running ingestion with a narrower
    # document set is the only way to do that today. `llm` is unused here
    # (see app/bpmn/builder.py's docstring -- generation is a pure,
    # deterministic transform of the already-extracted schema) but kept in
    # the signature for API consistency with the other generation-shaped
    # endpoints and in case a future refinement needs it.
    del llm

    schema = repository.get_process_schema(db, process_id)
    if schema is None or not schema.elements:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="No extracted process data yet -- upload and wait for at least one document to finish processing first",
        )

    xml, low_confidence_element_ids = build_bpmn_xml(process_id, schema)

    # US3.4: validate, but don't block on it -- a generated draft comes
    # from real (imperfect) extracted data, e.g. a multi-document merge
    # (Epic 1, US1.8) can leave a node disconnected. That's real data to
    # review and fix (chat editing, Epic 5), not a reason to withhold the
    # whole diagram. Issues ride along in the response (_to_schema) instead
    # of raising. A hard failure here (well-formedness, a crash) would
    # still propagate as a 500 -- only structural *issues* are tolerated.
    draft = repository.set_draft_bpmn(db, process_id, xml, low_confidence_element_ids)
    return _to_schema(draft)


@router.get("", response_model=BPMNDocument)
def get_draft_bpmn(process_id: str, db: DbDep, user: CurrentUserDep) -> BPMNDocument:
    del user
    draft = repository.get_draft_bpmn(db, process_id)
    if draft is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="No draft BPMN yet -- call /generate first")
    return _to_schema(draft)


@router.put("", response_model=BPMNDocument)
def update_draft_bpmn(process_id: str, body: BPMNUpdateRequest, db: DbDep, user: EditorDep) -> BPMNDocument:
    del user
    repository.get_process(db, process_id)  # 404s if missing

    if not body.xml.strip():
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="xml must not be empty")

    # US3.4: run the same validation checklist on a manual edit as on a
    # generated diagram -- this is the bpmn-chat-ops/Epic 4 (US4.2) TODO
    # this route used to carry, now resolved by reusing app/bpmn/validation.py
    # rather than only checking for non-empty input.
    issues = validate_bpmn(body.xml)
    if issues:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid BPMN: {issues}")

    # A manually-edited diagram has no per-element confidence data to carry
    # forward -- clear it rather than keep stale flags from a previous
    # generation that may no longer apply to the edited content.
    draft = repository.set_draft_bpmn(db, process_id, body.xml, [])
    return _to_schema(draft)
