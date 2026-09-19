import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, status

from app.bpmn.builder import _tag
from app.bpmn.chat_ops import humanize_validation_issues
from app.bpmn.validation import validate_bpmn_integrity
from app.db import repository
from app.schemas.versions import VersionDetail, VersionDiffResult, VersionSummary

from .deps import DbDep

router = APIRouter(prefix="/api/processes/{process_id}", tags=["versions"])


def _element_labels(xml_str: str) -> dict[str, str | None]:
    try:
        root = ET.fromstring(xml_str)
    except ET.ParseError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Invalid BPMN XML: {exc}") from exc

    # Only the semantic <bpmn:process> subtree -- iterating the whole
    # document (root.iter()) also picks up <bpmndi:BPMNShape>/BPMNEdge ids,
    # which are a different id space (e.g. "Shape_Task_c") and would show
    # up as spurious added/removed/changed entries in a diff. Invisible
    # with hand-written test XML that had no DI section; a real generated
    # diagram always does.
    process = root.find(_tag("bpmn", "process"))
    scope = process if process is not None else root

    labels: dict[str, str | None] = {}
    for el in scope.iter():
        element_id = el.get("id")
        if element_id:
            labels[element_id] = el.get("name")
    return labels


@router.post("/finalize", response_model=VersionSummary, status_code=status.HTTP_201_CREATED)
def finalize_process(process_id: str, db: DbDep) -> VersionSummary:
    process = repository.get_process(db, process_id)  # 404s if missing
    draft = repository.get_draft_bpmn(db, process_id)
    if draft is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No draft BPMN to finalize -- generate or edit one first")

    # Epic 11: content-completeness ("no incoming/outgoing flow") used to
    # be checked here directly against the generated XML -- that's now
    # gap analysis's job, run automatically after ingestion/edits
    # (app/gap_analysis/) and resolved by the user through the gap-review
    # UI, not a raw validator error at Finalize time. Two gates replace
    # the old single validate_bpmn call:
    if process.gap_analysis_completed_at is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail="Run gap analysis before finalizing -- this process has never been checked for "
            "structural or cross-document gaps",
        )

    open_findings = repository.list_gap_findings(db, process_id, status="open")
    if open_findings:
        questions = "; ".join(f.question for f in open_findings)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Cannot finalize -- {len(open_findings)} unresolved gap finding(s): {questions}",
        )

    # Deterministic backstop: is the generated XML itself well-formed and
    # renderable (should never legitimately fail if app/bpmn/builder.py is
    # correct -- not a content gap a user resolves through this epic's UI).
    issues = validate_bpmn_integrity(draft.xml)
    if issues:
        # Same treatment as the chat-apply error (app/api/chat.py) -- raw
        # issue strings quote internal BPMN ids ('Task_el_8f055...'),
        # meaningless to the Process Analyst clicking Finalize. Swap in
        # element/flow/lane labels when a schema exists to resolve them
        # against; a manually-edited draft (PUT /bpmn) can have no schema
        # at all, in which case humanize_validation_issues leaves ids as-is.
        schema = repository.get_process_schema(db, process_id)
        readable_issues = humanize_validation_issues(issues, schema) if schema else issues
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST, detail="Cannot finalize invalid BPMN: " + "; ".join(readable_issues)
        )

    version = repository.add_version(db, process_id, draft.xml)
    return VersionSummary(**version.model_dump(exclude={"xml"}))


@router.get("/versions", response_model=list[VersionSummary])
def list_versions(process_id: str, db: DbDep) -> list[VersionSummary]:
    repository.get_process(db, process_id)  # 404s if missing
    return [VersionSummary(**v.model_dump(exclude={"xml"})) for v in repository.list_versions(db, process_id)]


@router.get("/versions/diff", response_model=VersionDiffResult)
def diff_versions(
    process_id: str, db: DbDep, from_version_id: str = Query(...), to_version_id: str = Query(...)
) -> VersionDiffResult:
    # Registered before /versions/{version_id} -- FastAPI matches routes in
    # registration order, and a static path must be declared before a
    # path-param sibling that would otherwise swallow it (e.g. version_id="diff").
    repository.get_process(db, process_id)  # 404s if missing
    from_version = repository.get_version(db, process_id, from_version_id)
    to_version = repository.get_version(db, process_id, to_version_id)

    from_labels = _element_labels(from_version.xml)
    to_labels = _element_labels(to_version.xml)

    added = sorted(set(to_labels) - set(from_labels))
    removed = sorted(set(from_labels) - set(to_labels))
    changed = sorted(eid for eid in set(from_labels) & set(to_labels) if from_labels[eid] != to_labels[eid])
    labels = {eid: to_labels.get(eid, from_labels.get(eid)) for eid in {*added, *removed, *changed}}

    return VersionDiffResult(
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        added_element_ids=added,
        removed_element_ids=removed,
        changed_element_ids=changed,
        labels=labels,
    )


@router.get("/versions/{version_id}", response_model=VersionDetail)
def get_version(process_id: str, version_id: str, db: DbDep) -> VersionDetail:
    repository.get_process(db, process_id)  # 404s if missing
    return repository.get_version(db, process_id, version_id)


@router.post("/versions/{version_id}/restore", response_model=VersionSummary)
def restore_version(process_id: str, version_id: str, db: DbDep) -> VersionSummary:
    repository.get_process(db, process_id)  # 404s if missing
    version = repository.get_version(db, process_id, version_id)
    # A restored version was already validated at finalize time and has no
    # per-element confidence data of its own to carry forward. XML-only --
    # does not touch the process schema tables, see VersionModel's
    # docstring (app/db/models.py) for why that's a deliberate boundary.
    repository.set_draft_bpmn(db, process_id, version.xml, [])
    return VersionSummary(**version.model_dump(exclude={"xml"}))
