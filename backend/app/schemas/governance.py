"""Epic 37: approval status (US37.3-US37.5), the audit log (US37.6), and
the organization's approval policy (US37.5)."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, Field


class ChangeEntryOut(BaseModel):
    actor_id: str | None = None
    actor_name: str | None = None
    action: str
    label: str
    tier: str  # high | standard
    at: datetime | None = None


class ApprovalOut(BaseModel):
    approver_id: str | None = None
    approver_name: str | None = None
    decision: str
    comment: str | None = None
    at: datetime | None = None


class ApprovalStatus(BaseModel):
    """What the approval screen shows for one pending change set."""

    object_type: str  # process_version | agent
    object_id: str
    status: str
    required_approvals: int
    approvals: list[ApprovalOut] = []
    # Who changed what since the last approval, high-risk first.
    changes: list[ChangeEntryOut] = []
    # For the signed-in user: may they approve, and if not, why not.
    can_approve: bool
    reason: str | None = None


class ApprovalDecision(BaseModel):
    comment: str | None = None


class AuditEvent(BaseModel):
    id: str
    at: datetime
    actor_type: str
    actor_id: str | None = None
    actor_name: str | None = None
    action: str
    object_type: str | None = None
    object_id: str | None = None
    process_id: str | None = None
    outcome: str
    ip: str | None = None
    detail: dict = {}


class GovernanceSettings(BaseModel):
    required_agent_approvals: int = Field(default=1, ge=1, le=5)
    required_agent_approvals_by_unit: dict[str, int] = {}
