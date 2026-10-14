"""Epic 37: API views of users and approval status, shared by the auth,
versions, agents and governance routes."""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import sod
from app.auth.permissions import Permission, effective_permissions, has_permission, missing_permission_message
from app.db.models import AgentArtifactModel, ApprovalModel, ContextEntityModel, UserModel, VersionModel
from app.schemas.auth import RoleGrant, UserOut
from app.schemas.governance import ApprovalOut, ApprovalStatus, ChangeEntryOut


def user_out(session: Session, user: UserModel) -> UserOut:
    grants = []
    for row in sorted(user.roles, key=lambda r: (r.scope_type, r.scope_id, r.role)):
        scope_name = None
        if row.scope_type == "business_unit":
            unit = session.get(ContextEntityModel, row.scope_id)
            scope_name = unit.name if unit is not None else row.scope_id
        grants.append(
            RoleGrant(
                role=row.role, scope_type=row.scope_type, scope_id=row.scope_id, scope_name=scope_name, source=row.source
            )
        )
    return UserOut(
        id=user.id,
        name=user.name,
        active=user.active,
        roles=grants,
        permissions=effective_permissions(user),
        created_at=user.created_at,
    )


def _name(session: Session, user_id: str | None) -> str | None:
    if user_id is None:
        return None
    user = session.get(UserModel, user_id)
    return user.name if user is not None else None


def _changes(entries: list[sod.ChangeEntry]) -> list[ChangeEntryOut]:
    ordered = [e for e in entries if e.tier == sod.HIGH] + [e for e in entries if e.tier != sod.HIGH]
    return [
        ChangeEntryOut(actor_id=e.actor_id, actor_name=e.actor_name, action=e.action, label=e.label, tier=e.tier, at=e.at)
        for e in ordered
    ]


def _approval_out(session: Session, row: ApprovalModel) -> ApprovalOut:
    return ApprovalOut(
        approver_id=row.approver_id,
        approver_name=_name(session, row.approver_id),
        decision=row.decision,
        comment=row.comment,
        at=row.at,
    )


def version_approval_status(session: Session, version: VersionModel, user: UserModel) -> ApprovalStatus:
    entries = sod.version_change_set(session, version)
    decisions = session.scalars(
        select(ApprovalModel)
        .where(ApprovalModel.object_type == "process_version", ApprovalModel.object_id == version.id)
        .order_by(ApprovalModel.at)
    )
    reason = None
    if not has_permission(session, user, Permission.PROCESS_APPROVE, process_id=version.process_id):
        reason = missing_permission_message(Permission.PROCESS_APPROVE)
    elif version.status != "pending_approval":
        reason = f"This version is {version.status.replace('_', ' ')}"
    else:
        reason = sod.ineligibility_reason(entries, user.id, subject="this baseline")
    return ApprovalStatus(
        object_type="process_version",
        object_id=version.id,
        status=version.status,
        required_approvals=1,
        approvals=[_approval_out(session, d) for d in decisions],
        changes=_changes(entries),
        can_approve=reason is None,
        reason=reason,
    )


def agent_approval_status(session: Session, artifact: AgentArtifactModel, user: UserModel) -> ApprovalStatus:
    entries = sod.agent_change_set(session, artifact)
    votes = sod.current_agent_votes(session, artifact)
    reason = None
    if not has_permission(session, user, Permission.AGENT_APPROVE, process_id=artifact.process_id):
        reason = missing_permission_message(Permission.AGENT_APPROVE)
    elif artifact.approved_at is not None:
        reason = "This agent version is already approved"
    elif any(v.approver_id == user.id for v in votes):
        reason = "You have already approved this agent version -- it needs another approver"
    else:
        reason = sod.ineligibility_reason(entries, user.id, subject="this agent version")
    return ApprovalStatus(
        object_type="agent",
        object_id=artifact.id,
        status="approved" if artifact.approved_at is not None else "pending_approval",
        required_approvals=sod.required_agent_approvals(session, artifact.process_id),
        approvals=[_approval_out(session, v) for v in votes],
        changes=_changes(entries),
        can_approve=reason is None,
        reason=reason,
    )
