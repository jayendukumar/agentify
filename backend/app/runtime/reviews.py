"""Epic 39, US39.10: the review inbox for live cases.

Two kinds of task:

- **checkpoint**: the agent asked for a person's decision before acting.
  The execution waits (WAITING_FOR_HUMAN). Approving or rejecting it
  resumes the execution through the same HumanPort path a manual twin
  checkpoint uses; the decision, who made it and their comment go on the
  step and in the audit log. Rejecting needs a comment.
- **escalation**: the agent couldn't finish the case (its escalation
  policy, a limit, an open circuit, a restart after a write). The
  execution stays ESCALATED; a reviewer resolves the case by hand and
  records the outcome, which is attached to it.

Who takes it: the approval matrix (Epic 27/30 approval authorities that
apply to the process) gives levels, lowest limit first, skipping
authorities whose limit the proposed amount exceeds. Without a matrix,
the escalation policy's `escalation_target.role`. The role is a name
shown on the task; acting on it needs the `runtime.review` permission
for the process's business unit. A task past its due time moves to the
next level, and the deployment's owners and the process's reviewers are
notified. Nobody may decide a task for a case they asked for
(`requested_by`).

Every decision is a labelled outcome: monitoring reads it (the
rejection pause rule, US39.12), and it can be added to a labelled dataset
(Epic 32) as a golden case.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import audit
from app.auth.permissions import Permission, has_permission
from app.context import repository as context_repository
from app.db.models import (
    AgentDeploymentModel,
    AgentExecutionModel,
    ReviewTaskModel,
    RuntimeNotificationModel,
    UserModel,
)
from app.ids import new_id, utcnow
from app.schemas.agents import AgentDefinition

AMOUNT_FIELDS = ("amount", "total", "value", "limit", "amount_due", "invoice_amount")


class ReviewError(Exception):
    def __init__(self, status: int, detail: str):
        super().__init__(detail)
        self.status = status
        self.detail = detail


def _now():
    return utcnow().replace(tzinfo=None)


# ---- who reviews ---------------------------------------------------------------------------


def _amount(action: dict[str, Any]) -> float | None:
    for key, value in action.items():
        if key.lower() in AMOUNT_FIELDS and isinstance(value, (int, float)):
            return float(value)
        if isinstance(value, dict):
            nested = _amount(value)
            if nested is not None:
                return nested
    return None


def review_levels(session: Session, process_id: str, definition: AgentDefinition, action: dict[str, Any]) -> list[str]:
    """Roles in escalation order for this case."""
    scope = context_repository.get_process_scope(session, process_id)
    authorities = [(a.data or {}) for a in context_repository.applicable_entities(session, scope,
                                                                                  kinds=["approval_authority"])]
    levels = _matrix_roles(authorities, _amount(action))
    target = definition.escalation_policy.escalation_target
    if not levels and target is not None and target.role:
        levels = [target.role]
    return levels


def _matrix_roles(authorities: list[dict[str, Any]], amount: float | None) -> list[str]:
    """Lowest limit first; no-limit authorities last; none the amount exceeds."""
    def covers(data: dict[str, Any]) -> bool:
        limit = data.get("limit_amount")
        return amount is None or limit is None or amount <= limit

    ordered = sorted(authorities, key=lambda d: (d.get("limit_amount") is None, d.get("limit_amount") or 0))
    roles = [d.get("role_name") for d in ordered if covers(d) and d.get("role_name")]
    return list(dict.fromkeys(roles))


def reviewers(session: Session, process_id: str) -> list[UserModel]:
    users = session.scalars(select(UserModel).where(UserModel.active.is_(True)))
    return [u for u in users if has_permission(session, u, Permission.RUNTIME_REVIEW, process_id=process_id)]


def notify(session: Session, user_ids: set[str], *, kind: str, message: str, process_id: str | None = None,
           deployment_id: str | None = None, task_id: str | None = None) -> None:
    for user_id in sorted(u for u in user_ids if u):
        session.add(RuntimeNotificationModel(
            id=new_id("note"), user_id=user_id, kind=kind, message=message, process_id=process_id,
            deployment_id=deployment_id, task_id=task_id,
        ))
    session.flush()


def owners(deployment: AgentDeploymentModel) -> set[str]:
    return {u for u in (deployment.created_by, deployment.approved_by) if u}


# ---- creating tasks --------------------------------------------------------------------------


def _open_task(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel, *, kind: str,
               summary: str, action: dict[str, Any]) -> ReviewTaskModel:
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    levels = review_levels(session, deployment.process_id, definition, action)
    task = ReviewTaskModel(
        id=new_id("rtask"), execution_id=execution.id, deployment_id=deployment.id, process_id=deployment.process_id,
        kind=kind, status="open", summary=summary, proposed_action=action, case_input=execution.input or {},
        assignee_role=levels[0] if levels else None, level=0,
        due_at=_now() + timedelta(minutes=deployment.review_sla_minutes),
    )
    session.add(task)
    session.flush()
    return task


def open_checkpoint(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> ReviewTaskModel:
    pending = ((execution.resume_state or {}).get("pending_calls") or [{}])[0]
    arguments = pending.get("arguments") or {}
    action = arguments.get("proposed_action") if isinstance(arguments.get("proposed_action"), dict) else {}
    return _open_task(session, execution, deployment, kind="checkpoint",
                      summary=str(arguments.get("summary") or "The agent asks for a review before acting"), action=action)


def open_escalation(session: Session, execution: AgentExecutionModel, deployment: AgentDeploymentModel) -> ReviewTaskModel:
    reasons = [d.get("reason") for d in execution.deviations or [] if d.get("reason")]
    return _open_task(session, execution, deployment, kind="escalation",
                      summary=reasons[-1] if reasons else "The agent passed this case on", action={})


def cancel_open(session: Session, deployment_id: str, reason: str) -> int:
    tasks = list(session.scalars(select(ReviewTaskModel).where(ReviewTaskModel.deployment_id == deployment_id,
                                                               ReviewTaskModel.status == "open")))
    for task in tasks:
        task.status = "cancelled"
        task.comment = reason
    session.flush()
    return len(tasks)


# ---- deciding ------------------------------------------------------------------------------------


def _check_reviewer(session: Session, task: ReviewTaskModel, user: UserModel) -> AgentExecutionModel:
    if task.status != "open":
        raise ReviewError(409, f"This task is already {task.status}")
    execution = session.get(AgentExecutionModel, task.execution_id)
    requester = (execution.requested_by or "").strip().lower()
    if requester and requester in {(user.name or "").lower(), (user.email or "").lower()}:
        raise ReviewError(403, "You asked for this case -- someone else must review it")
    return execution


def decide(session: Session, task: ReviewTaskModel, user: UserModel, *, decision: str, comment: str | None) -> None:
    """Records a checkpoint decision; the caller queues the resume."""
    if task.kind != "checkpoint":
        raise ReviewError(409, "An escalated case is resolved, not approved or rejected")
    if decision == "reject" and not (comment or "").strip():
        raise ReviewError(422, "Say why you're rejecting it")
    execution = _check_reviewer(session, task, user)
    if execution.status != "WAITING_FOR_HUMAN":
        raise ReviewError(409, f"The case is {execution.status}, not waiting for a decision")
    task.status, task.decision, task.comment = "decided", decision, (comment or "").strip() or None
    task.decided_by, task.decided_at = user.id, _now()
    audit.record(session, actor=user, action="runtime_review_decided", object_type="agent_execution",
                 object_id=execution.id, process_id=task.process_id,
                 detail={"task_id": task.id, "decision": decision, "comment": task.comment})
    session.flush()


def note_for(task: ReviewTaskModel, user: UserModel) -> str:
    verb = "Approved" if task.decision == "approve" else "Rejected"
    return f"{verb} by {user.name}" + (f": {task.comment}" if task.comment else "")


def resolve(session: Session, task: ReviewTaskModel, user: UserModel, *, outcome: dict[str, Any],
            comment: str | None) -> None:
    """A reviewer closes an escalated case by hand."""
    if task.kind != "escalation":
        raise ReviewError(409, "A checkpoint is approved or rejected, not resolved")
    if not outcome:
        raise ReviewError(422, "Record the outcome you reached")
    execution = _check_reviewer(session, task, user)
    task.status, task.outcome, task.comment = "resolved", outcome, (comment or "").strip() or None
    task.decision = outcome.get("decision") if outcome.get("decision") in ("approve", "reject") else None
    task.decided_by, task.decided_at = user.id, _now()
    execution.resolution = {"outcome": outcome, "comment": task.comment, "by": user.id, "by_name": user.name,
                            "at": task.decided_at.isoformat()}
    audit.record(session, actor=user, action="runtime_case_resolved", object_type="agent_execution",
                 object_id=execution.id, process_id=task.process_id, detail={"task_id": task.id, "outcome": outcome})
    session.flush()


# ---- SLAs -----------------------------------------------------------------------------------------


def escalate_overdue(session: Session) -> int:
    """Moves each overdue open task to the next level of its matrix and
    tells the deployment's owners and the process's reviewers."""
    now = _now()
    tasks = list(session.scalars(select(ReviewTaskModel).where(ReviewTaskModel.status == "open",
                                                               ReviewTaskModel.due_at <= now)))
    for task in tasks:
        deployment = session.get(AgentDeploymentModel, task.deployment_id)
        definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
        levels = review_levels(session, task.process_id, definition, task.proposed_action or {})
        task.level += 1
        if task.level < len(levels):
            task.assignee_role = levels[task.level]
            where = f"now with {task.assignee_role}"
        else:
            where = "at the top of the approval matrix"
        task.escalated_at = now
        task.due_at = now + timedelta(minutes=deployment.review_sla_minutes)
        message = (f"A review for {deployment.snapshot['definition']['name']} is overdue and was escalated ({where}): "
                   f"{task.summary}")
        notify(session, owners(deployment) | {u.id for u in reviewers(session, task.process_id)},
               kind="review_overdue", message=message, process_id=task.process_id, deployment_id=deployment.id,
               task_id=task.id)
    session.flush()
    return len(tasks)


# ---- feedback --------------------------------------------------------------------------------------


def to_dataset_row(task: ReviewTaskModel) -> dict[str, Any]:
    """US39.10/Epic 32: the case and the person's answer, as a golden row."""
    if task.status not in ("decided", "resolved"):
        raise ReviewError(409, "Only a decided or resolved task can become a dataset row")
    outputs = {k: v for k, v in (task.outcome or {}).items() if k != "decision"}
    labels: dict[str, Any] = {"outputs": outputs}
    if task.decision:
        labels["decision"] = task.decision
    return {"inputs": task.case_input or {}, "labels": labels, "segment": "production", "provenance": "expert",
            "tags": ["from-production", task.kind, task.id], "critical": task.decision == "reject"}
