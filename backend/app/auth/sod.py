"""Epic 37, US37.3-US37.5: contribution history and segregation of duties.

The rule: nobody approves a change they made. Two kinds of change are
high-risk -- amending the business process and amending an agent
definition -- and any such change, however small, needs a fresh approval
from someone who didn't make it. Every other change (test assets, runs,
release-candidate marking) is recorded at the standard tier: shown on the
approval screen, never disqualifying.

A *change set* is an object's contributions since its last approval:
- a process version contains the process's contributions after the
  previous approved baseline, up to the moment it was finalized;
- an agent's pending change set is its contributions after the last
  approval (`approved_through_seq`).

HIGH_RISK_ACTIONS is the policy, as data: moving an action into the
high-risk tier is a one-line change here. A high-risk contribution also
invalidates what was approved (record_contribution).
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import (
    AgentArtifactModel,
    ApprovalModel,
    ContributionModel,
    OrganizationModel,
    ProcessModel,
    UserModel,
    VersionModel,
)
from app.ids import new_id, utcnow

HIGH = "high"
STANDARD = "standard"

HIGH_RISK_ACTIONS: frozenset[tuple[str, str]] = frozenset(
    {
        ("process", "draft_generated"),
        ("process", "draft_edited"),
        ("process", "chat_edit_applied"),
        ("process", "gap_resolution_applied"),
        ("process", "version_restored"),
        ("agent", "generated"),
        ("agent", "regenerated"),
        ("agent", "governance_edited"),
    }
)

ACTION_LABELS: dict[str, str] = {
    "draft_generated": "generated the diagram",
    "draft_edited": "edited the diagram",
    "chat_edit_applied": "applied a chat edit to the diagram",
    "gap_resolution_applied": "applied a gap resolution to the diagram",
    "version_restored": "restored an earlier version into the draft",
    "generated": "generated the agent definition",
    "regenerated": "regenerated the agent definition",
    "governance_edited": "edited the agent definition",
}

# The request-scoped marker app/api/deps.py reads so a route that already
# recorded a specific contribution doesn't also get a generic one.
_RECORDED_KEY = "epic37_contributed"


class SegregationOfDutiesError(Exception):
    """Refused: the actor contributed to the change they're approving."""


class ApprovalStateError(Exception):
    """Refused: the object isn't in a state this decision applies to."""


def tier_for(object_type: str, action: str) -> str:
    return HIGH if (object_type, action) in HIGH_RISK_ACTIONS else STANDARD


def action_label(action: str) -> str:
    return ACTION_LABELS.get(action, action.replace("_", " "))


def record_contribution(
    session: Session,
    *,
    object_type: str,
    object_id: str,
    process_id: str | None,
    actor_id: str | None,
    action: str,
    detail: dict[str, Any] | None = None,
) -> ContributionModel:
    tier = tier_for(object_type, action)
    actor = session.get(UserModel, actor_id) if actor_id else None
    row = ContributionModel(
        object_type=object_type,
        object_id=object_id,
        process_id=process_id,
        actor_type=actor.actor_type if actor is not None else "system",
        actor_id=actor_id,
        action=action,
        tier=tier,
        detail=detail or {},
    )
    session.add(row)
    session.flush()
    session.info.setdefault(_RECORDED_KEY, set()).add((object_type, object_id))
    if tier == HIGH:
        _invalidate(session, object_type, object_id)
    return row


def already_recorded(session: Session, object_type: str, object_id: str) -> bool:
    return (object_type, object_id) in session.info.get(_RECORDED_KEY, set())


def _invalidate(session: Session, object_type: str, object_id: str) -> None:
    """A high-risk change voids what was approved: the agent drops back
    below RELEASE_CANDIDATE (its test evidence was for the old definition
    too), and a process's pending baseline is superseded by the edit."""
    if object_type == "agent":
        artifact = session.get(AgentArtifactModel, object_id)
        if artifact is not None:
            artifact.approved_at = None
            artifact.approved_by = None
            artifact.marked_release_candidate_at = None
    elif object_type == "process":
        for version in session.scalars(
            select(VersionModel).where(VersionModel.process_id == object_id, VersionModel.status == "pending_approval")
        ):
            version.status = "superseded"
    session.flush()


# -- change sets --------------------------------------------------------------


@dataclass
class ChangeEntry:
    seq: int
    actor_id: str | None
    actor_name: str | None
    action: str
    label: str
    tier: str
    at: datetime | None


def _entries(session: Session, rows: list[ContributionModel]) -> list[ChangeEntry]:
    names: dict[str, str] = {}
    out = []
    for row in rows:
        if row.actor_id and row.actor_id not in names:
            user = session.get(UserModel, row.actor_id)
            names[row.actor_id] = user.name if user is not None else row.actor_id
        out.append(
            ChangeEntry(
                seq=row.seq,
                actor_id=row.actor_id,
                actor_name=names.get(row.actor_id) if row.actor_id else None,
                action=row.action,
                label=action_label(row.action),
                tier=row.tier,
                at=row.at,
            )
        )
    return out


def _contributions(
    session: Session, object_type: str, object_id: str, *, after: int, upto: int | None = None
) -> list[ContributionModel]:
    query = select(ContributionModel).where(
        ContributionModel.object_type == object_type,
        ContributionModel.object_id == object_id,
        ContributionModel.seq > after,
    )
    if upto is not None:
        query = query.where(ContributionModel.seq <= upto)
    return list(session.scalars(query.order_by(ContributionModel.seq)))


def max_seq(session: Session, object_type: str, object_id: str, *, tier: str | None = None) -> int:
    query = select(func.max(ContributionModel.seq)).where(
        ContributionModel.object_type == object_type, ContributionModel.object_id == object_id
    )
    if tier is not None:
        query = query.where(ContributionModel.tier == tier)
    return session.scalar(query) or 0


def _previous_approved(session: Session, version: VersionModel) -> VersionModel | None:
    return session.scalar(
        select(VersionModel)
        .where(
            VersionModel.process_id == version.process_id,
            VersionModel.status == "approved",
            VersionModel.created_at < version.created_at,
            VersionModel.id != version.id,
        )
        .order_by(VersionModel.created_at.desc())
        .limit(1)
    )


def version_change_set(session: Session, version: VersionModel) -> list[ChangeEntry]:
    previous = _previous_approved(session, version)
    floor = previous.contribution_seq if previous is not None else 0
    rows = _contributions(session, "process", version.process_id, after=floor, upto=version.contribution_seq)
    return _entries(session, rows)


def agent_change_set(session: Session, artifact: AgentArtifactModel) -> list[ChangeEntry]:
    rows = _contributions(session, "agent", artifact.id, after=artifact.approved_through_seq or 0)
    entries = _entries(session, rows)
    if not entries and artifact.approved_through_seq is None and artifact.generated_by:
        # Generated before Epic 37 recorded contributions: whoever last
        # generated it is the one contributor we know of.
        user = session.get(UserModel, artifact.generated_by)
        entries = [
            ChangeEntry(
                seq=0,
                actor_id=artifact.generated_by,
                actor_name=user.name if user is not None else None,
                action="generated",
                label=action_label("generated"),
                tier=HIGH,
                at=artifact.generated_at,
            )
        ]
    return entries


def ineligibility_reason(entries: list[ChangeEntry], user_id: str, *, subject: str) -> str | None:
    mine = [e for e in entries if e.tier == HIGH and e.actor_id == user_id]
    if not mine:
        return None
    latest = mine[-1]
    when = f" on {latest.at:%d %b %Y}" if latest.at else ""
    return f"You {latest.label}{when} -- someone else must approve {subject}"


# -- process baselines ----------------------------------------------------------


def finalize_seq(session: Session, process_id: str) -> int:
    """The last contribution a version finalized now contains."""
    return max_seq(session, "process", process_id)


def supersede_pending(session: Session, process_id: str) -> None:
    for version in session.scalars(
        select(VersionModel).where(VersionModel.process_id == process_id, VersionModel.status == "pending_approval")
    ):
        version.status = "superseded"
    session.flush()


def decide_version(
    session: Session, version: VersionModel, user: UserModel, *, decision: str, comment: str | None = None
) -> None:
    if version.status != "pending_approval":
        raise ApprovalStateError(f"This version is {version.status.replace('_', ' ')}, not awaiting approval")
    if decision == "approved":
        reason = ineligibility_reason(version_change_set(session, version), user.id, subject="this baseline")
        if reason:
            raise SegregationOfDutiesError(reason)
    session.add(
        ApprovalModel(
            id=new_id("appr"),
            object_type="process_version",
            object_id=version.id,
            process_id=version.process_id,
            through_seq=version.contribution_seq,
            approver_id=user.id,
            decision=decision,
            comment=comment,
        )
    )
    version.status = decision
    version.decided_at = utcnow()
    version.decided_by = user.id
    session.flush()


# -- agent releases -------------------------------------------------------------


def required_agent_approvals(session: Session, process_id: str) -> int:
    """US37.5: the business unit's setting if it has one, else the
    organization's, else 1."""
    organization = session.scalar(select(OrganizationModel).limit(1))
    governance = (organization.governance if organization is not None else None) or {}
    process = session.get(ProcessModel, process_id)
    by_unit = governance.get("required_agent_approvals_by_unit") or {}
    if process is not None and process.business_unit_id and process.business_unit_id in by_unit:
        return max(1, int(by_unit[process.business_unit_id]))
    return max(1, int(governance.get("required_agent_approvals") or 1))


def current_agent_votes(session: Session, artifact: AgentArtifactModel) -> list[ApprovalModel]:
    """Approvals that still count: cast after the last high-risk change."""
    through = max_seq(session, "agent", artifact.id, tier=HIGH)
    rows = session.scalars(
        select(ApprovalModel)
        .where(
            ApprovalModel.object_type == "agent",
            ApprovalModel.object_id == artifact.id,
            ApprovalModel.decision == "approved",
            ApprovalModel.through_seq == through,
        )
        .order_by(ApprovalModel.at)
    )
    votes, seen = [], set()
    for row in rows:
        if row.approver_id not in seen:
            seen.add(row.approver_id)
            votes.append(row)
    return votes


def approve_agent(session: Session, artifact: AgentArtifactModel, user: UserModel, *, comment: str | None = None) -> bool:
    """Records one approval; returns True once enough independent approvers
    have approved the current change set (the agent is then APPROVED)."""
    if artifact.approved_at is not None:
        raise ApprovalStateError("This agent version is already approved")
    reason = ineligibility_reason(agent_change_set(session, artifact), user.id, subject="this agent version")
    if reason:
        raise SegregationOfDutiesError(reason)
    votes = current_agent_votes(session, artifact)
    if any(v.approver_id == user.id for v in votes):
        raise ApprovalStateError("You have already approved this agent version -- it needs another approver")
    through = max_seq(session, "agent", artifact.id, tier=HIGH)
    session.add(
        ApprovalModel(
            id=new_id("appr"),
            object_type="agent",
            object_id=artifact.id,
            process_id=artifact.process_id,
            through_seq=through,
            approver_id=user.id,
            decision="approved",
            comment=comment,
        )
    )
    session.flush()
    if len(votes) + 1 < required_agent_approvals(session, artifact.process_id):
        return False
    artifact.approved_at = utcnow()
    artifact.approved_by = user.id
    artifact.approved_through_seq = max_seq(session, "agent", artifact.id)
    session.flush()
    return True


def check_publisher(session: Session, artifact: AgentArtifactModel, user: UserModel) -> None:
    """US37.4: a publication needs an approval by someone other than the
    publisher."""
    if artifact.approved_at is None:
        raise ApprovalStateError("Approve this agent version before publishing it")
    approvers = {v.approver_id for v in current_agent_votes(session, artifact)}
    if not approvers and artifact.approved_by:
        approvers = {artifact.approved_by}  # approved before Epic 37 recorded votes
    if approvers and approvers <= {user.id}:
        raise SegregationOfDutiesError("You approved this agent version -- someone else must publish it")
