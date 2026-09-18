import xml.etree.ElementTree as ET

from fastapi import APIRouter, HTTPException, Query, status

from app.bpmn.builder import _tag
from app.bpmn.validation import validate_bpmn
from app.db import repository
from app.ids import new_id, utcnow
from app.schemas.versions import VersionDetail, VersionDiffResult, VersionSummary

from .deps import DbDep, StoreDep

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
def finalize_process(process_id: str, db: DbDep, store: StoreDep) -> VersionSummary:
    repository.get_process(db, process_id)  # 404s if missing
    draft = repository.get_draft_bpmn(db, process_id)
    if draft is None:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="No draft BPMN to finalize -- generate or edit one first")

    # Resolves this story's original TODO: the full bpmn-authoring
    # validation checklist (app/bpmn/validation.py, built for Epic 3,
    # US3.4), not just "is it well-formed XML".
    issues = validate_bpmn(draft.xml)
    if issues:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Cannot finalize invalid BPMN: {issues}")

    version = VersionDetail(id=new_id("ver"), process_id=process_id, label=None, created_at=utcnow(), xml=draft.xml)
    store.add_version(process_id, version)
    return VersionSummary(**version.model_dump(exclude={"xml"}))


@router.get("/versions", response_model=list[VersionSummary])
def list_versions(process_id: str, db: DbDep, store: StoreDep) -> list[VersionSummary]:
    repository.get_process(db, process_id)  # 404s if missing
    return [VersionSummary(**v.model_dump(exclude={"xml"})) for v in store.list_versions(process_id)]


@router.get("/versions/diff", response_model=VersionDiffResult)
def diff_versions(
    process_id: str, db: DbDep, store: StoreDep, from_version_id: str = Query(...), to_version_id: str = Query(...)
) -> VersionDiffResult:
    # Registered before /versions/{version_id} -- FastAPI matches routes in
    # registration order, and a static path must be declared before a
    # path-param sibling that would otherwise swallow it (e.g. version_id="diff").
    repository.get_process(db, process_id)  # 404s if missing
    from_version = store.get_version(process_id, from_version_id)
    to_version = store.get_version(process_id, to_version_id)

    from_labels = _element_labels(from_version.xml)
    to_labels = _element_labels(to_version.xml)

    added = sorted(set(to_labels) - set(from_labels))
    removed = sorted(set(from_labels) - set(to_labels))
    changed = sorted(eid for eid in set(from_labels) & set(to_labels) if from_labels[eid] != to_labels[eid])

    return VersionDiffResult(
        from_version_id=from_version_id,
        to_version_id=to_version_id,
        added_element_ids=added,
        removed_element_ids=removed,
        changed_element_ids=changed,
    )


@router.get("/versions/{version_id}", response_model=VersionDetail)
def get_version(process_id: str, version_id: str, db: DbDep, store: StoreDep) -> VersionDetail:
    repository.get_process(db, process_id)  # 404s if missing
    return store.get_version(process_id, version_id)


@router.post("/versions/{version_id}/restore", response_model=VersionSummary)
def restore_version(process_id: str, version_id: str, db: DbDep, store: StoreDep) -> VersionSummary:
    repository.get_process(db, process_id)  # 404s if missing
    version = store.get_version(process_id, version_id)
    # A restored version was already validated at finalize time and has no
    # per-element confidence data of its own to carry forward.
    repository.set_draft_bpmn(db, process_id, version.xml, [])
    return VersionSummary(**version.model_dump(exclude={"xml"}))
