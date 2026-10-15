"""Epic 27: persistence for the organization context store -- the
organization itself, context entities (with in-place versioning and
history rows), policy documents, and policy passages.

Kept out of app/db/repository.py (already ~2,200 lines) because the
context store is its own domain that later epics (28-31) build on; the
conventions are the same -- functions take a Session, raise
NotFoundError, and return pydantic models.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    ContextDocumentModel,
    ContextEntityModel,
    ContextEntityVersionModel,
    OrganizationModel,
    PolicyPassageModel,
    ProcessModel,
)
from app.ids import new_id, utcnow
from app.schemas.context import (
    DATA_MODELS,
    AppliesTo,
    ApplicableContext,
    BulkConfirmResult,
    Citation,
    ContextDocument,
    ContextEntity,
    ContextEntityCreate,
    ContextEntityUpdate,
    ContextEntityVersion,
    Organization,
    OrganizationUpdate,
    PolicyPassage,
    ProcessScope,
)
from app.store import NotFoundError

from .applicability import applies, make_parent_lookup
from .jurisdictions import normalize_code


class ContextValidationError(Exception):
    """A write that would leave the context store inconsistent -- mapped
    to a 400 by the API layer."""


# ---- organization ---------------------------------------------------------


def get_organization_model(session: Session) -> OrganizationModel:
    """US27.1: one organization per tenant (Epic 38), created lazily the first
    time anything asks for it so every other feature can assume it
    exists."""
    organization = session.scalars(select(OrganizationModel).order_by(OrganizationModel.created_at)).first()
    if organization is None:
        organization = OrganizationModel(id=new_id("org"), name="My organization", profile={})
        session.add(organization)
        session.flush()
    return organization


def to_organization(organization: OrganizationModel) -> Organization:
    return Organization(
        id=organization.id,
        name=organization.name,
        industry=organization.industry,
        size=organization.size,
        profile=organization.profile or {},
        applied_packs=dict((organization.profile or {}).get("applied_packs", {})),
        created_at=organization.created_at,
        updated_at=organization.updated_at,
    )


def get_organization(session: Session) -> Organization:
    return to_organization(get_organization_model(session))


def update_organization(session: Session, data: OrganizationUpdate) -> Organization:
    organization = get_organization_model(session)
    organization.name = data.name
    organization.industry = data.industry
    organization.size = data.size
    session.flush()
    return to_organization(organization)


def set_organization_profile_value(session: Session, key: str, value: Any) -> None:
    organization = get_organization_model(session)
    profile = dict(organization.profile or {})
    profile[key] = value
    organization.profile = profile  # reassign: JSON columns don't track in-place mutation
    session.flush()


# ---- entities -------------------------------------------------------------


def validate_data(kind: str, data: dict[str, Any]) -> dict[str, Any]:
    try:
        return DATA_MODELS[kind].model_validate(data).model_dump()
    except ValidationError as exc:
        raise ContextValidationError(f"Invalid data for a {kind}: {exc}") from exc


def to_entity(entity: ContextEntityModel) -> ContextEntity:
    return ContextEntity(
        id=entity.id,
        kind=entity.kind,  # type: ignore[arg-type]
        name=entity.name,
        code=entity.code,
        data=entity.data or {},
        applies_to=AppliesTo.model_validate(entity.applies_to or {}),
        status=entity.status,  # type: ignore[arg-type]
        confidence=entity.confidence,
        citations=[Citation.model_validate(c) for c in entity.citations or []],
        source=entity.source,
        parent_id=entity.parent_id,
        supersedes_id=entity.supersedes_id,
        relaxes=entity.relaxes,
        owner_business_unit_id=entity.owner_business_unit_id,
        version=entity.version,
        created_at=entity.created_at,
        updated_at=entity.updated_at,
        confirmed_by=entity.confirmed_by,
        confirmed_at=entity.confirmed_at,
    )


def _snapshot(entity: ContextEntityModel) -> dict[str, Any]:
    return {
        "kind": entity.kind,
        "name": entity.name,
        "code": entity.code,
        "data": entity.data,
        "applies_to": entity.applies_to,
        "citations": entity.citations,
        "source": entity.source,
        "parent_id": entity.parent_id,
        "owner_business_unit_id": entity.owner_business_unit_id,
    }


def _record_version(
    session: Session, entity: ContextEntityModel, *, changed_by: str | None, change_note: str | None = None
) -> None:
    session.add(
        ContextEntityVersionModel(
            id=new_id("ctxver"),
            entity_id=entity.id,
            version=entity.version,
            snapshot=_snapshot(entity),
            change_note=change_note,
            changed_by=changed_by,
        )
    )


def get_entity_model(session: Session, entity_id: str) -> ContextEntityModel:
    entity = session.get(ContextEntityModel, entity_id)
    if entity is None:
        raise NotFoundError("context entity", entity_id)
    return entity


def get_entity(session: Session, entity_id: str) -> ContextEntity:
    return to_entity(get_entity_model(session, entity_id))


def create_entity(
    session: Session,
    data: ContextEntityCreate,
    *,
    status: str = "confirmed",
    source: str = "manual",
    confidence: float | None = None,
    citations: list[Citation] | None = None,
    supersedes_id: str | None = None,
    relaxes: bool = False,
    created_by: str | None = None,
) -> ContextEntityModel:
    if data.parent_id is not None:
        get_entity_model(session, data.parent_id)
    code = normalize_code(data.code) if data.kind == "jurisdiction" and data.code else data.code
    entity = ContextEntityModel(
        id=new_id("ctx"),
        kind=data.kind,
        code=code,
        name=data.name,
        data=validate_data(data.kind, data.data),
        applies_to=data.applies_to.model_dump(),
        status=status,
        confidence=confidence,
        citations=[c.model_dump() for c in citations or []],
        source=source,
        parent_id=data.parent_id,
        supersedes_id=supersedes_id,
        relaxes=relaxes,
        owner_business_unit_id=data.owner_business_unit_id,
        version=1,
        created_by=created_by,
    )
    if status == "confirmed":
        entity.confirmed_by = created_by
        entity.confirmed_at = utcnow()
    session.add(entity)
    session.flush()
    if status == "confirmed":
        _record_version(session, entity, changed_by=created_by, change_note="created")
        session.flush()
    return entity


def list_entities(
    session: Session,
    *,
    kind: str | None = None,
    status: str | list[str] | None = "confirmed",
    parent_id: str | None = None,
) -> list[ContextEntityModel]:
    query = select(ContextEntityModel)
    if kind is not None:
        query = query.where(ContextEntityModel.kind == kind)
    if isinstance(status, str):
        query = query.where(ContextEntityModel.status == status)
    elif status is not None:
        query = query.where(ContextEntityModel.status.in_(status))
    if parent_id is not None:
        query = query.where(ContextEntityModel.parent_id == parent_id)
    return list(session.scalars(query.order_by(ContextEntityModel.kind, ContextEntityModel.name)))


def find_entity(session: Session, kind: str, *, code: str | None = None, name: str | None = None) -> ContextEntityModel | None:
    """The live (confirmed) entity of `kind` matching `code`, else `name`
    (case-insensitive) -- used to dedup extraction, wizard answers, and
    starter packs against what already exists."""
    candidates = list_entities(session, kind=kind, status="confirmed")
    if code:
        wanted = normalize_code(code) if kind == "jurisdiction" else code.strip().lower()
        for entity in candidates:
            have = entity.code if kind == "jurisdiction" else (entity.code or "").strip().lower()
            if entity.code and have == wanted:
                return entity
    if name:
        for entity in candidates:
            if entity.name.strip().lower() == name.strip().lower():
                return entity
    return None


def _apply_update(entity: ContextEntityModel, data: ContextEntityUpdate) -> bool:
    changed = False
    if data.name is not None and data.name != entity.name:
        entity.name, changed = data.name, True
    if data.code is not None and data.code != entity.code:
        entity.code, changed = data.code, True
    if data.data is not None:
        validated = validate_data(entity.kind, data.data)
        if validated != entity.data:
            entity.data, changed = validated, True
    if data.applies_to is not None and data.applies_to.model_dump() != entity.applies_to:
        entity.applies_to, changed = data.applies_to.model_dump(), True
    if data.parent_id is not None and data.parent_id != entity.parent_id:
        entity.parent_id, changed = data.parent_id, True
    if data.owner_business_unit_id is not None and data.owner_business_unit_id != entity.owner_business_unit_id:
        entity.owner_business_unit_id, changed = data.owner_business_unit_id, True
    return changed


def update_entity(
    session: Session, entity_id: str, data: ContextEntityUpdate, *, changed_by: str | None = None
) -> ContextEntityModel:
    """Notes: versioned in place -- a real change bumps `version` and, for
    a confirmed entity, writes a history row. A no-op save doesn't bump."""
    entity = get_entity_model(session, entity_id)
    if entity.status in ("dismissed", "retired"):
        raise ContextValidationError(f"Cannot edit a {entity.status} {entity.kind}")
    if data.parent_id is not None:
        get_entity_model(session, data.parent_id)
    if not _apply_update(entity, data):
        return entity
    entity.version += 1
    session.flush()
    if entity.status == "confirmed":
        _record_version(session, entity, changed_by=changed_by, change_note=data.change_note)
        session.flush()
    return entity


def retire_entity(session: Session, entity_id: str, *, changed_by: str | None = None) -> ContextEntityModel:
    entity = get_entity_model(session, entity_id)
    entity.status = "retired"
    entity.version += 1
    session.flush()
    _record_version(session, entity, changed_by=changed_by, change_note="retired")
    session.flush()
    return entity


def list_entity_versions(session: Session, entity_id: str) -> list[ContextEntityVersion]:
    get_entity_model(session, entity_id)
    rows = session.scalars(
        select(ContextEntityVersionModel)
        .where(ContextEntityVersionModel.entity_id == entity_id)
        .order_by(ContextEntityVersionModel.version)
    )
    return [
        ContextEntityVersion(
            entity_id=row.entity_id,
            version=row.version,
            snapshot=row.snapshot,
            change_note=row.change_note,
            changed_at=row.changed_at,
            changed_by=row.changed_by,
        )
        for row in rows
    ]


def get_entity_version_snapshot(session: Session, entity_id: str, version: int) -> dict[str, Any] | None:
    row = session.scalars(
        select(ContextEntityVersionModel).where(
            ContextEntityVersionModel.entity_id == entity_id, ContextEntityVersionModel.version == version
        )
    ).first()
    return row.snapshot if row else None


# ---- confirmation (US27.6) ------------------------------------------------


def confirm_entity(
    session: Session, entity_id: str, *, acknowledge_relaxation: bool = False, confirmed_by: str | None = None
) -> ContextEntityModel:
    """A plain proposal becomes confirmed. A proposal that supersedes an
    existing entity is merged into it instead -- the existing entity gets
    the new content as its next version, so everything that references
    it keeps referencing it -- and the proposal row is removed."""
    entity = get_entity_model(session, entity_id)
    if entity.status != "proposed":
        raise ContextValidationError(f"Only a proposed entity can be confirmed (this one is {entity.status})")
    if entity.relaxes and not acknowledge_relaxation:
        raise ContextValidationError(
            "This proposal would loosen an existing rule -- confirm it explicitly with acknowledge_relaxation"
        )

    if entity.supersedes_id is not None:
        target = get_entity_model(session, entity.supersedes_id)
        target.name = entity.name
        target.code = entity.code or target.code
        target.data = entity.data
        target.applies_to = entity.applies_to
        target.citations = entity.citations
        target.source = entity.source
        target.confidence = entity.confidence
        target.version += 1
        target.confirmed_by = confirmed_by
        target.confirmed_at = utcnow()
        session.flush()
        _record_version(session, target, changed_by=confirmed_by, change_note=f"confirmed update from {entity.source}")
        session.delete(entity)
        session.flush()
        return target

    entity.status = "confirmed"
    entity.confirmed_by = confirmed_by
    entity.confirmed_at = utcnow()
    session.flush()
    _record_version(session, entity, changed_by=confirmed_by, change_note=f"confirmed from {entity.source}")
    session.flush()
    return entity


def dismiss_entity(session: Session, entity_id: str) -> ContextEntityModel:
    entity = get_entity_model(session, entity_id)
    if entity.status != "proposed":
        raise ContextValidationError(f"Only a proposed entity can be dismissed (this one is {entity.status})")
    entity.status = "dismissed"
    session.flush()
    return entity


def bulk_confirm(
    session: Session, *, ids: list[str] | None, min_confidence: float, confirmed_by: str | None = None
) -> BulkConfirmResult:
    """Notes: a low-confidence proposal, or one that would loosen an
    existing control, is never bulk-confirmable -- it is skipped with the
    reason, for one-at-a-time review."""
    proposals = list_entities(session, status="proposed")
    if ids is not None:
        wanted = set(ids)
        proposals = [p for p in proposals if p.id in wanted]
    result = BulkConfirmResult()
    for proposal in proposals:
        if proposal.relaxes:
            result.skipped[proposal.id] = "would loosen an existing rule -- review it individually"
            continue
        if proposal.confidence is not None and proposal.confidence < min_confidence:
            result.skipped[proposal.id] = f"low confidence ({proposal.confidence:.2f})"
            continue
        confirmed = confirm_entity(session, proposal.id, confirmed_by=confirmed_by)
        result.confirmed.append(confirmed.id)
    return result


# ---- policy documents and passages ----------------------------------------


def to_context_document(document: ContextDocumentModel) -> ContextDocument:
    return ContextDocument(
        id=document.id,
        filename=document.filename,
        content_type=document.content_type,
        size_bytes=document.size_bytes,
        status=document.status,  # type: ignore[arg-type]
        error_message=document.error_message,
        policy_entity_id=document.policy_entity_id,
        proposal_count=document.proposal_count,
        created_at=document.created_at,
    )


def get_context_document_model(session: Session, document_id: str) -> ContextDocumentModel:
    document = session.get(ContextDocumentModel, document_id)
    if document is None:
        raise NotFoundError("policy document", document_id)
    return document


def list_context_documents(session: Session) -> list[ContextDocument]:
    rows = session.scalars(select(ContextDocumentModel).order_by(ContextDocumentModel.created_at.desc()))
    return [to_context_document(row) for row in rows]


def to_passage(passage: PolicyPassageModel) -> PolicyPassage:
    return PolicyPassage(
        id=passage.id,
        document_id=passage.document_id,
        policy_entity_id=passage.policy_entity_id,
        policy_version=passage.policy_version,
        clause_ref=passage.clause_ref,
        text=passage.text,
        applies_to=AppliesTo.model_validate(passage.applies_to or {}),
        current=passage.current,
    )


def list_passages(session: Session, *, document_id: str | None = None, policy_entity_id: str | None = None) -> list[PolicyPassage]:
    query = select(PolicyPassageModel)
    if document_id is not None:
        query = query.where(PolicyPassageModel.document_id == document_id)
    if policy_entity_id is not None:
        query = query.where(PolicyPassageModel.policy_entity_id == policy_entity_id)
    return [to_passage(p) for p in session.scalars(query.order_by(PolicyPassageModel.ordinal))]


# ---- scope and applicability (US27.2, US27.9) -----------------------------


def jurisdiction_parent_lookup(session: Session):
    declared = {
        entity.code: (entity.data or {}).get("parent_code")
        for entity in list_entities(session, kind="jurisdiction")
        if entity.code
    }
    return make_parent_lookup(declared)


def get_process_scope(session: Session, process_id: str) -> ProcessScope:
    process = session.get(ProcessModel, process_id)
    if process is None:
        raise NotFoundError("process", process_id)
    return ProcessScope(
        business_unit_id=process.business_unit_id,
        jurisdictions=list(process.jurisdictions or []),
        process_type=process.process_type,
        data_classes=list(process.data_classes or []),
    )


def set_process_scope(session: Session, process_id: str, scope: ProcessScope) -> ProcessScope:
    process = session.get(ProcessModel, process_id)
    if process is None:
        raise NotFoundError("process", process_id)
    if scope.business_unit_id is not None:
        unit = get_entity_model(session, scope.business_unit_id)
        if unit.kind != "business_unit":
            raise ContextValidationError(f"'{scope.business_unit_id}' is not a business unit")
    process.business_unit_id = scope.business_unit_id
    process.jurisdictions = sorted({normalize_code(code) for code in scope.jurisdictions if code.strip()})
    process.process_type = scope.process_type or None
    process.data_classes = sorted(set(scope.data_classes))
    session.flush()
    return get_process_scope(session, process_id)


def applicable_entities(
    session: Session, scope: ProcessScope, *, kinds: list[str] | None = None, systems: list[str] | None = None
) -> list[ContextEntityModel]:
    parent_of = jurisdiction_parent_lookup(session)
    result = []
    for entity in list_entities(session, status="confirmed"):
        if kinds is not None and entity.kind not in kinds:
            continue
        if applies(AppliesTo.model_validate(entity.applies_to or {}), scope, parent_of, systems or ()):
            result.append(entity)
    return result


def applicable_context(session: Session, scope: ProcessScope) -> ApplicableContext:
    grouped: dict[str, list[ContextEntity]] = {}
    for entity in applicable_entities(session, scope):
        grouped.setdefault(entity.kind, []).append(to_entity(entity))
    return ApplicableContext(scope=scope, entities=grouped)
