"""Epic 27: the organization context API -- organization profile and
wizard, context entities with confirm/dismiss, starter packs, policy
documents, and the applicable-context browser. See planning/epics/27-
organization-context-foundation.md.

Epic 31 adds federated access (US31.7: users see and steward only their
units' context, app/context/access.py), the context access audit log
(US31.10), and dispatch through the durable job queue when its worker is
running (US31.1).
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, File, Form, HTTPException, Query, Request, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.auth import audit as auth_audit
from app.auth.permissions import Permission, may_act_in_unit
from app.config import get_settings
from app.context import access as context_access
from app.context import audit, consumers, ingestion, packs, profile, repository, resolver
from app.db import repository as db_repository
from app.db.models import ContextDocumentModel, ContextEntityModel, ContextEntityVersionModel, UserBusinessUnitModel, UserModel
from app.ids import new_id
from app.jobs import queue
from app.llm import LLMClient
from app.schemas.context import (
    AppliesTo,
    ApplicableContext,
    BulkConfirmRequest,
    BulkConfirmResult,
    ConfirmRequest,
    ContextDocument,
    ContextEntity,
    ContextEntityCreate,
    ContextEntityUpdate,
    ContextEntityVersion,
    ContextImpact,
    ContextPack,
    LlmPolicy,
    Organization,
    OrganizationProfileAnswers,
    OrganizationUpdate,
    PolicyPassage,
    ProcessScope,
    ProfileResult,
    StarterPackApplyResult,
    StarterPackSummary,
)

from .deps import ContextConfirmDep, ContextEditDep, CurrentUserDep, DbDep, LLMDep, OrgAdminDep, ProcessEditDep

router = APIRouter(prefix="/api/organization", tags=["organization"])

_MAX_SIZE_BYTES = 25 * 1024 * 1024


def _dispatch_runs(background_tasks: BackgroundTasks, db, llm: LLMClient, queued: list[tuple[str, str]]) -> None:
    """Runs go on the durable queue when its worker is running (Epic 31,
    US31.1), else in-process background tasks as before."""
    if not queued:
        return
    if get_settings().job_worker_enabled:
        for run_id, kind in queued:
            queue.enqueue(db, "twin_run" if kind == "twin" else "orchestration_run", {"run_id": run_id})
        db.commit()
        return
    db.commit()
    for run_id, kind in queued:
        if kind == "twin":
            background_tasks.add_task(db_repository.execute_twin_run_background, run_id, llm)
        else:
            background_tasks.add_task(db_repository.execute_orchestration_run_background, run_id, llm)


def _rerun_affected(
    background_tasks: BackgroundTasks, db, llm: LLMClient, entity_ids: list[str], user_id: str | None
) -> None:
    """US28.8: a context change re-runs the regression suites of agents
    built against the changed entity."""
    if not get_settings().context_auto_rerun_suites:
        return
    _dispatch_runs(background_tasks, db, llm, consumers.start_affected_suite_runs(db, entity_ids, run_by=user_id))


@router.get("", response_model=Organization)
def get_organization(db: DbDep, user: CurrentUserDep) -> Organization:
    del user
    return repository.get_organization(db)


@router.put("", response_model=Organization)
def update_organization(body: OrganizationUpdate, db: DbDep, user: OrgAdminDep) -> Organization:
    context_access.require_unrestricted(context_access.access_for(db, user))
    return repository.update_organization(db, body)


@router.put("/llm-policy", response_model=Organization)
def set_llm_policy(body: LlmPolicy, db: DbDep, user: OrgAdminDep) -> Organization:
    """Epic 31, US31.8: the organization-wide model policy, used where a
    business unit has none of its own."""
    context_access.require_unrestricted(context_access.access_for(db, user))
    repository.set_organization_profile_value(db, "llm_policy", body.model_dump())
    return repository.get_organization(db)


@router.post("/profile", response_model=ProfileResult)
def apply_profile(body: OrganizationProfileAnswers, db: DbDep, user: ContextEditDep) -> ProfileResult:
    context_access.require_unrestricted(context_access.access_for(db, user))
    return profile.apply_profile(db, body, created_by=user.id)


# ---- starter packs (US27.7) -----------------------------------------------


@router.get("/starter-packs", response_model=list[StarterPackSummary])
def list_starter_packs(db: DbDep, user: CurrentUserDep) -> list[StarterPackSummary]:
    del user
    return packs.list_pack_summaries(db)


@router.post("/starter-packs/{pack_id}/apply", response_model=StarterPackApplyResult)
def apply_starter_pack(
    pack_id: str, db: DbDep, llm: LLMDep, user: ContextEditDep, background_tasks: BackgroundTasks
) -> StarterPackApplyResult:
    context_access.require_unrestricted(context_access.access_for(db, user))
    result = packs.apply_pack(db, pack_id, created_by=user.id)
    if result.updated:
        source = f"starter_pack:{result.pack_id}@{result.version}"
        changed = [e.id for e in repository.list_entities(db, status="confirmed") if e.source == source and e.version > 1]
        _rerun_affected(background_tasks, db, llm, changed, user.id)
    return result


# ---- entities -------------------------------------------------------------


@router.get("/entities", response_model=list[ContextEntity])
def list_entities(
    db: DbDep,
    user: CurrentUserDep,
    kind: str | None = None,
    status_filter: str | None = Query(default="confirmed", alias="status"),
) -> list[ContextEntity]:
    """`status=all` lists every status; a comma-separated list filters to
    several (e.g. `proposed,confirmed`). Only context the user may see."""
    statuses: str | list[str] | None
    if status_filter in (None, "", "all"):
        statuses = None
    elif "," in status_filter:
        statuses = [s.strip() for s in status_filter.split(",") if s.strip()]
    else:
        statuses = status_filter
    access = context_access.access_for(db, user)
    return [
        repository.to_entity(e)
        for e in repository.list_entities(db, kind=kind, status=statuses)
        if context_access.can_see(access, e)
    ]


@router.post("/entities", response_model=ContextEntity, status_code=status.HTTP_201_CREATED)
def create_entity(body: ContextEntityCreate, db: DbDep, user: ContextEditDep) -> ContextEntity:
    owner = context_access.owner_for_new(context_access.access_for(db, user), body.owner_business_unit_id)
    body = body.model_copy(update={"owner_business_unit_id": owner})
    return repository.to_entity(repository.create_entity(db, body, created_by=user.id))


@router.get("/entities/{entity_id}", response_model=ContextEntity)
def get_entity(entity_id: str, db: DbDep, user: CurrentUserDep) -> ContextEntity:
    entity = repository.get_entity_model(db, entity_id)
    context_access.require_visible(context_access.access_for(db, user), entity)
    audit.record(db, actor_type="user", actor=user.id, action="view_entity", entity_ids=[entity.id])
    return repository.to_entity(entity)


@router.put("/entities/{entity_id}", response_model=ContextEntity)
def update_entity(
    entity_id: str, body: ContextEntityUpdate, db: DbDep, llm: LLMDep, user: ContextEditDep, background_tasks: BackgroundTasks
) -> ContextEntity:
    existing = repository.get_entity_model(db, entity_id)
    context_access.require_steward(context_access.access_for(db, user), existing)
    before = existing.version
    entity = repository.update_entity(db, entity_id, body, changed_by=user.id)
    if entity.version != before:
        _rerun_affected(background_tasks, db, llm, [entity.id], user.id)
    return repository.to_entity(entity)


@router.delete("/entities/{entity_id}", response_model=ContextEntity)
def retire_entity(
    entity_id: str, db: DbDep, llm: LLMDep, user: ContextEditDep, background_tasks: BackgroundTasks
) -> ContextEntity:
    """Retire rather than delete: pinned context packs (Epic 28) may still
    reference this entity's history."""
    context_access.require_steward(context_access.access_for(db, user), repository.get_entity_model(db, entity_id))
    entity = repository.retire_entity(db, entity_id, changed_by=user.id)
    _rerun_affected(background_tasks, db, llm, [entity.id], user.id)
    return repository.to_entity(entity)


@router.get("/entities/{entity_id}/impact", response_model=ContextImpact)
def entity_impact(entity_id: str, db: DbDep, user: CurrentUserDep) -> ContextImpact:
    """US28.8 / Epic 31 US31.9: which blueprints, agents, and suites were
    built against this entity."""
    context_access.require_visible(context_access.access_for(db, user), repository.get_entity_model(db, entity_id))
    return consumers.compute_impact(db, entity_id)


@router.post("/entities/{entity_id}/rerun-affected", response_model=ContextImpact)
def rerun_affected(
    entity_id: str, db: DbDep, llm: LLMDep, user: ContextEditDep, background_tasks: BackgroundTasks
) -> ContextImpact:
    context_access.require_visible(context_access.access_for(db, user), repository.get_entity_model(db, entity_id))
    impact = consumers.compute_impact(db, entity_id)
    queued = consumers.start_affected_suite_runs(db, [entity_id], run_by=user.id)
    _dispatch_runs(background_tasks, db, llm, queued)
    db.commit()
    return impact


@router.get("/entities/{entity_id}/versions", response_model=list[ContextEntityVersion])
def list_entity_versions(entity_id: str, db: DbDep, user: CurrentUserDep) -> list[ContextEntityVersion]:
    context_access.require_visible(context_access.access_for(db, user), repository.get_entity_model(db, entity_id))
    return repository.list_entity_versions(db, entity_id)


@router.post("/entities/{entity_id}/confirm", response_model=ContextEntity)
def confirm_entity(
    entity_id: str, body: ConfirmRequest, db: DbDep, llm: LLMDep, user: ContextConfirmDep, background_tasks: BackgroundTasks
) -> ContextEntity:
    proposal = repository.get_entity_model(db, entity_id)
    access = context_access.access_for(db, user)
    context_access.require_steward(access, proposal)
    if proposal.supersedes_id is not None:
        context_access.require_steward(access, repository.get_entity_model(db, proposal.supersedes_id))
    supersedes = proposal.supersedes_id
    entity = repository.confirm_entity(
        db, entity_id, acknowledge_relaxation=body.acknowledge_relaxation, confirmed_by=user.id
    )
    if supersedes is not None:
        _rerun_affected(background_tasks, db, llm, [entity.id], user.id)
    return repository.to_entity(entity)


@router.post("/entities/{entity_id}/dismiss", response_model=ContextEntity)
def dismiss_entity(entity_id: str, db: DbDep, user: ContextEditDep) -> ContextEntity:
    context_access.require_steward(context_access.access_for(db, user), repository.get_entity_model(db, entity_id))
    return repository.to_entity(repository.dismiss_entity(db, entity_id))


@router.post("/entities/bulk-confirm", response_model=BulkConfirmResult)
def bulk_confirm(body: BulkConfirmRequest, db: DbDep, user: ContextConfirmDep) -> BulkConfirmResult:
    access = context_access.access_for(db, user)
    ids = body.ids
    if not access.unrestricted:
        allowed = [p.id for p in repository.list_entities(db, status="proposed") if context_access.can_steward(access, p)]
        ids = [i for i in (ids or allowed) if i in allowed]
    return repository.bulk_confirm(db, ids=ids, min_confidence=body.min_confidence, confirmed_by=user.id)


# ---- policy documents (US27.6) --------------------------------------------


@router.post("/documents", response_model=ContextDocument, status_code=status.HTTP_201_CREATED)
async def upload_policy_document(
    db: DbDep,
    llm: LLMDep,
    user: ContextEditDep,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    title: str = Form(default=""),
    code: str = Form(default=""),
    business_unit_id: str = Form(default=""),
    jurisdictions: str = Form(default=""),
    effective_date: str = Form(default=""),
) -> ContextDocument:
    """One policy document per upload. The policy entity is created (or,
    when `code` matches an existing policy, re-versioned) immediately; the
    extraction runs in the background."""
    settings = get_settings()
    access = context_access.access_for(db, user)
    owner = context_access.owner_for_new(access, business_unit_id or None) if not access.unrestricted else None
    content_type = file.content_type or "application/octet-stream"
    if (file.filename or "").lower().endswith((".md", ".markdown")):
        content_type = "text/markdown"
    if content_type not in ingestion.SUPPORTED_POLICY_CONTENT_TYPES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"Unsupported content type '{content_type}'")
    data = await file.read()
    if len(data) > _MAX_SIZE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File exceeds the 25MB limit")

    filename = file.filename or "policy"
    document_id = new_id("ctxdoc")
    scope = AppliesTo(
        business_units=[business_unit_id] if business_unit_id else [],
        jurisdictions=[j.strip() for j in jurisdictions.split(",") if j.strip()],
    )
    policy_data = {"summary": "", "document_id": document_id, "effective_date": effective_date or None}
    existing = repository.find_entity(db, "policy", code=code or None, name=None) if code else None
    if existing is not None:
        context_access.require_steward(access, existing)
        # Re-uploading the exact current document would only re-extract the
        # same rules in new words (observed live) -- nothing to review.
        current_doc_id = (existing.data or {}).get("document_id")
        current_doc = db.get(ContextDocumentModel, current_doc_id) if current_doc_id else None
        if current_doc is not None and current_doc.content_hash == ingestion.content_hash(data):
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                detail=f"This exact document is already the current version of {existing.code or existing.name}",
            )
        policy = repository.update_entity(
            db,
            existing.id,
            ContextEntityUpdate(
                name=title or existing.name, data=policy_data, applies_to=scope, change_note=f"new document {filename}"
            ),
            changed_by=user.id,
        )
    else:
        policy = repository.create_entity(
            db,
            ContextEntityCreate(
                kind="policy",
                name=title or filename,
                code=code or None,
                data=policy_data,
                applies_to=scope,
                owner_business_unit_id=owner,
            ),
            source=f"document:{document_id}",
            created_by=user.id,
        )

    ingestion.save_policy_file(settings, document_id, filename, data)
    document = ContextDocumentModel(
        id=document_id,
        filename=filename,
        content_type=content_type,
        size_bytes=len(data),
        status="pending",
        policy_entity_id=policy.id,
        content_hash=ingestion.content_hash(data),
        created_by=user.id,
    )
    db.add(document)
    db.flush()
    if settings.job_worker_enabled:
        queue.enqueue(db, "policy_ingest", {"document_id": document_id}, dedupe_key=f"ingest:{document_id}")
        db.commit()
    else:
        db.commit()  # the background task opens its own session -- see app/api/documents.py
        background_tasks.add_task(ingestion.process_policy_document, document_id, llm, settings)
    return repository.to_context_document(document)


def _document_visible(db, access: context_access.Access, document: ContextDocumentModel) -> bool:
    if access.unrestricted or document.policy_entity_id is None:
        return True
    policy = db.get(ContextEntityModel, document.policy_entity_id)
    return policy is None or context_access.can_see(access, policy)


@router.get("/documents", response_model=list[ContextDocument])
def list_policy_documents(db: DbDep, user: CurrentUserDep) -> list[ContextDocument]:
    access = context_access.access_for(db, user)
    return [
        repository.to_context_document(d)
        for d in db.scalars(select(ContextDocumentModel).order_by(ContextDocumentModel.created_at.desc()))
        if _document_visible(db, access, d)
    ]


@router.get("/documents/{document_id}", response_model=ContextDocument)
def get_policy_document(document_id: str, db: DbDep, user: CurrentUserDep) -> ContextDocument:
    document = repository.get_context_document_model(db, document_id)
    if not _document_visible(db, context_access.access_for(db, user), document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"policy document '{document_id}' not found")
    return repository.to_context_document(document)


@router.get("/documents/{document_id}/passages", response_model=list[PolicyPassage])
def list_document_passages(document_id: str, db: DbDep, user: CurrentUserDep) -> list[PolicyPassage]:
    document = repository.get_context_document_model(db, document_id)
    if not _document_visible(db, context_access.access_for(db, user), document):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"policy document '{document_id}' not found")
    audit.record(
        db, actor_type="user", actor=user.id, action="view_policy_text",
        entity_ids=[document.policy_entity_id] if document.policy_entity_id else [],
    )
    return repository.list_passages(db, document_id=document_id)


# ---- scope and browser (US27.2, US27.9) -----------------------------------


@router.get("/applicable", response_model=ApplicableContext)
def applicable_context(
    db: DbDep,
    user: CurrentUserDep,
    business_unit_id: str | None = None,
    jurisdictions: str = "",
    process_type: str | None = None,
    data_classes: str = "",
) -> ApplicableContext:
    scope = ProcessScope(
        business_unit_id=business_unit_id or None,
        jurisdictions=[j.strip() for j in jurisdictions.split(",") if j.strip()],
        process_type=process_type or None,
        data_classes=[d.strip() for d in data_classes.split(",") if d.strip()],
    )
    access = context_access.access_for(db, user)
    result = repository.applicable_context(db, scope)
    if not access.unrestricted:
        result.entities = {
            kind: [e for e in entities if context_access.can_see(access, db.get(ContextEntityModel, e.id))]
            for kind, entities in result.entities.items()
        }
    return result


# ---- federation, changes, audit (Epic 31) ---------------------------------


class Membership(BaseModel):
    business_unit_id: str
    steward: bool = False


class MemberAccess(BaseModel):
    user_id: str
    name: str
    # Epic 37: role names the user holds at any scope.
    roles: list[str] = []
    memberships: list[Membership] = []


@router.get("/members", response_model=list[MemberAccess])
def list_members(db: DbDep, user: CurrentUserDep) -> list[MemberAccess]:
    """US31.7: who belongs to which business unit."""
    del user
    rows = list(db.scalars(select(UserBusinessUnitModel)))
    return [
        MemberAccess(
            user_id=u.id,
            name=u.name,
            roles=sorted({r.role for r in u.roles}),
            memberships=[Membership(business_unit_id=r.business_unit_id, steward=r.steward) for r in rows if r.user_id == u.id],
        )
        for u in db.scalars(select(UserModel).order_by(UserModel.name))
    ]


@router.put("/members/{user_id}", response_model=MemberAccess)
def set_memberships(user_id: str, body: list[Membership], db: DbDep, user: OrgAdminDep) -> MemberAccess:
    """Only group-level (unrestricted) editors manage memberships. An empty
    list makes the user unrestricted again."""
    context_access.require_unrestricted(context_access.access_for(db, user))
    target = db.get(UserModel, user_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"user '{user_id}' not found")
    for membership in body:
        unit = repository.get_entity_model(db, membership.business_unit_id)
        if unit.kind != "business_unit":
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"'{unit.id}' is not a business unit")
    for row in db.scalars(select(UserBusinessUnitModel).where(UserBusinessUnitModel.user_id == user_id)):
        db.delete(row)
    db.flush()
    for membership in body:
        db.add(UserBusinessUnitModel(user_id=user_id, business_unit_id=membership.business_unit_id, steward=membership.steward))
    db.flush()
    return MemberAccess(
        user_id=target.id, name=target.name, roles=sorted({r.role for r in target.roles}), memberships=body
    )


class ContextChange(BaseModel):
    entity_id: str
    kind: str
    name: str
    version: int
    change_note: str | None = None
    changed_at: str
    impact: ContextImpact


@router.get("/changes", response_model=list[ContextChange])
def recent_changes(db: DbDep, user: CurrentUserDep, limit: int = Query(default=20, ge=1, le=100)) -> list[ContextChange]:
    """US31.9: incoming context changes, each summarized as impact
    ("AML-004 v8 -- 3 blueprints, 5 agents, 2 regression suites affected")."""
    access = context_access.access_for(db, user)
    rows = db.scalars(
        select(ContextEntityVersionModel)
        .where(ContextEntityVersionModel.version > 1)
        .order_by(ContextEntityVersionModel.changed_at.desc())
        .limit(limit * 3)
    )
    changes: list[ContextChange] = []
    for row in rows:
        entity = db.get(ContextEntityModel, row.entity_id)
        if entity is None or not context_access.can_see(access, entity):
            continue
        changes.append(
            ContextChange(
                entity_id=entity.id,
                kind=entity.kind,
                name=entity.code or entity.name,
                version=row.version,
                change_note=row.change_note,
                changed_at=row.changed_at.isoformat(),
                impact=consumers.compute_impact(db, entity.id),
            )
        )
        if len(changes) >= limit:
            break
    return changes


@router.get("/audit-log", response_model=list[audit.AccessLogEntry])
def audit_log(
    db: DbDep,
    user: CurrentUserDep,
    limit: int = Query(default=200, ge=1, le=1000),
    actor_type: str | None = None,
    process_id: str | None = None,
    entity_id: str | None = None,
) -> list[audit.AccessLogEntry]:
    """US31.10: group-level access only -- the log itself reveals context."""
    context_access.require_unrestricted(context_access.access_for(db, user))
    return audit.list_entries(db, limit=limit, actor_type=actor_type, process_id=process_id, entity_id=entity_id)


pack_router = APIRouter(prefix="/api/context-packs", tags=["organization"])


@pack_router.get("/{pack_id}", response_model=ContextPack)
def get_context_pack(pack_id: str, db: DbDep, user: CurrentUserDep) -> ContextPack:
    """US28.9: the exact pack an output was produced with."""
    pack = resolver.get_pack_model(db, pack_id)
    audit.record(
        db, actor_type="user", actor=user.id, action="view_pack", pack_id=pack.id, process_id=pack.process_id,
        entity_ids=[e["entity_id"] for e in pack.entries],
    )
    return resolver.to_pack(db, pack)


process_router = APIRouter(prefix="/api/processes/{process_id}/scope", tags=["organization"])


@process_router.get("", response_model=ProcessScope)
def get_process_scope(process_id: str, db: DbDep, user: CurrentUserDep) -> ProcessScope:
    del user
    return repository.get_process_scope(db, process_id)


@process_router.put("", response_model=ProcessScope)
def set_process_scope(
    process_id: str, body: ProcessScope, db: DbDep, request: Request, user: ProcessEditDep
) -> ProcessScope:
    access = context_access.access_for(db, user)
    if not access.unrestricted and body.business_unit_id is not None and body.business_unit_id not in access.units:
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail="You can only scope a process to a unit you belong to")
    # Epic 37, US37.2: moving a process changes who may approve it, so the
    # destination must be within the mover's own role scope too.
    if not may_act_in_unit(user, Permission.PROCESS_EDIT, body.business_unit_id):
        reason = "You can only move a process into a unit your role covers"
        auth_audit.record_refusal(actor=user, action="set_process_scope", reason=reason, object_type="process",
                                  object_id=process_id, process_id=process_id, request=request)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=reason)
    return repository.set_process_scope(db, process_id, body)


@process_router.get("/context-pack", response_model=ContextPack)
def preview_context_pack(
    process_id: str, db: DbDep, user: CurrentUserDep, purpose: str = "blueprint_evaluation", query: str = ""
) -> ContextPack:
    """US28.9: what a call site would receive for this process right now."""
    pack = resolver.resolve_for_process(db, process_id, purpose=purpose, query_text=query, audit_llm=False)
    audit.record(db, actor_type="user", actor=user.id, action="preview_pack", pack_id=pack.id, process_id=process_id)
    return resolver.to_pack(db, pack)
