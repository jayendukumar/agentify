"""Epic 39: the agent runtime's routes.

Two audiences:

- **People** (`/api/...`, session and permission guarded): deploy an
  approved agent version, bind its tools, approve and activate it, stop
  it, and read its executions. Stopping one agent, every deployment of an
  agent, or every agent in the tenant is one action each (US39.11).
- **The tenant's own systems** (`/runtime/v1/...`): start an execution
  and read its result (US39.4). A system signs in as an OAuth client of
  the tenant's own IdP: it gets an access token through the client
  credentials grant, checked here against the IdP's keys, issuer, expiry
  and our audience. A tenant without an IdP gives its systems a bearer
  token instead, the way the IdP reaches `/scim/v2`. A client limited to
  some deployments gets 404 for the others, as if they didn't exist.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select

from app.auth import audit
from app.auth.oidc import IdpUnavailable, OidcError, OidcProvider, get_oidc_provider
from app.config import get_settings
from app.auth.permissions import Permission, has_permission
from app.db import repository
from app.db.models import (
    ProcessModel,
    AgentDeploymentModel,
    AgentExecutionModel,
    AgentIdentityModel,
    ReviewTaskModel,
    RuntimeClientModel,
    RuntimeNotificationModel,
)
from app.ids import new_id, utcnow
from app.datasets import service as datasets
from app.runtime import credentials, deployments, drift, jobs, limits, metrics, monitoring, reviews, rollout
from app.runtime.adapters import UnsafeProductionEndpoint, check_production_url
from app.schemas.agents import AgentDefinition
from app.schemas.datasets import DatasetRowInput
from app.schemas.run import RunStep
from app.schemas.runtime import (
    ActivateIn,
    AddedRows,
    AdapterBinding,
    AdapterBindingIn,
    AgentIdentity,
    CredentialTest,
    DecisionIn,
    Deployment,
    DeploymentSettingsIn,
    DeploymentSummary,
    Execution,
    ExecutionAccepted,
    ExecutionIn,
    ExecutionSummary,
    MonitoringIn,
    OutcomeIn,
    PromoteIn,
    RollbackIn,
    RuntimeDeploymentView,
    RuntimeOverviewRow,
    Notification,
    ResolutionIn,
    ResumeIn,
    ReviewTask,
    RuntimeClient,
    RuntimeClientCreated,
    RuntimeClientIn,
    StopIn,
    StopResult,
    ToDatasetIn,
    ToolCircuit,
)

from .deps import (
    AgentApproveDep,
    AgentDeployDep,
    AgentTestDep,
    CurrentUserDep,
    DbDep,
    OrgAdminDep,
    ProcessScope,
    RuntimeReviewDep,
    RuntimeStopDep,
)

router = APIRouter(prefix="/api/processes/{process_id}", tags=["runtime"], dependencies=[ProcessScope])
tenant_router = APIRouter(prefix="/api/runtime", tags=["runtime"])
client_router = APIRouter(prefix="/runtime/v1", tags=["runtime"])


# ---- shapes -------------------------------------------------------------------------


def _summary(db, d: AgentDeploymentModel) -> DeploymentSummary:
    return DeploymentSummary(
        id=d.id, process_id=d.process_id, agent_artifact_id=d.agent_artifact_id,
        agent_name=d.snapshot["definition"]["name"], artifact_version=d.artifact_version, environment=d.environment,
        status=d.status, created_by=d.created_by, created_at=d.created_at, approved_by=d.approved_by,
        activated_at=d.activated_at, stopped_at=d.stopped_at, stop_reason=d.stop_reason,
        executions=jobs.counts_by_status(db, d.id), traffic_share=d.traffic_share,
        shadow_started_at=d.shadow_started_at, previous_deployment_id=d.previous_deployment_id,
    )


def _detail(db, d: AgentDeploymentModel) -> Deployment:
    identity = db.get(AgentIdentityModel, d.identity_id)
    definition = AgentDefinition.model_validate(d.snapshot["definition"])
    bindings = []
    for name, b in sorted(deployments.bindings_of(db, d.id).items()):
        bindings.append(AdapterBinding(
            system_name=name, application_id=b.application_id, base_url=b.base_url, operations=b.operations,
            tested_operations=sorted(d.snapshot["tool_schemas"][name]["api_operations"]), credential=b.credential,
            differences=deployments.operation_differences(db, d, b), accepted_differences=b.accepted_differences,
        ))
    return Deployment(
        **_summary(db, d).model_dump(),
        identity=AgentIdentity.model_validate(identity, from_attributes=True),
        bindings=bindings, contracts=[c.system_name for c in definition.tool_contracts],
        readiness=deployments.readiness(db, d) if d.status == "pending" else [],
        shadow_waiver=d.shadow_waiver, limits=d.limits or {}, pause_rules=d.pause_rules or {},
        review_sla_minutes=d.review_sla_minutes, when_paused=d.when_paused, paused_at=d.paused_at,
        pause_reason=d.pause_reason,
        circuits=[ToolCircuit.model_validate(c, from_attributes=True) for c in limits.circuits_of(db, d.id)],
        telemetry=d.telemetry or {}, alert_rules=d.alert_rules or [], drift_rule=drift.rule(d),
        has_baseline=bool(drift.baseline_of(d).get("available")),
    )


def _execution_summary(e: AgentExecutionModel) -> ExecutionSummary:
    return ExecutionSummary(
        id=e.id, deployment_id=e.deployment_id, artifact_version=e.artifact_version, status=e.status,
        attempt=e.attempt, step_count=e.step_count, total_cost_usd=e.total_cost_usd, total_tokens=e.total_tokens,
        created_at=e.created_at, completed_at=e.completed_at, trigger=e.trigger, shadow_of=e.shadow_of,
    )


def _deployment(db, process_id: str, deployment_id: str) -> AgentDeploymentModel:
    d = db.get(AgentDeploymentModel, deployment_id)
    if d is None or d.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Deployment '{deployment_id}' not found")
    return d


def _refused(exc: deployments.DeploymentError) -> HTTPException:
    return HTTPException(status.HTTP_409_CONFLICT, detail={"message": str(exc), "problems": exc.problems})


# ---- deployments ----------------------------------------------------------------------


@router.get("/deployments", response_model=list[DeploymentSummary])
def list_deployments(process_id: str, db: DbDep, user: CurrentUserDep) -> list[DeploymentSummary]:
    del user
    rows = db.scalars(select(AgentDeploymentModel).where(AgentDeploymentModel.process_id == process_id)
                      .order_by(AgentDeploymentModel.created_at.desc()))
    return [_summary(db, d) for d in rows]


@router.post("/agent-artifacts/{artifact_id}/deployments", response_model=Deployment, status_code=201)
def create_deployment(process_id: str, artifact_id: str, db: DbDep, user: AgentDeployDep) -> Deployment:
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    try:
        d = deployments.create(db, artifact, user)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.get("/deployments/{deployment_id}", response_model=Deployment)
def get_deployment(process_id: str, deployment_id: str, db: DbDep, user: CurrentUserDep) -> Deployment:
    del user
    return _detail(db, _deployment(db, process_id, deployment_id))


@router.put("/deployments/{deployment_id}/bindings/{system_name}", response_model=Deployment)
def set_binding(process_id: str, deployment_id: str, system_name: str, body: AdapterBindingIn, db: DbDep,
                user: AgentDeployDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.set_binding(db, d, system_name, deployments.BindingInput(
            base_url=body.base_url, operations=body.operations, credential=body.credential,
            accept_differences=body.accept_differences), user)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/bindings/{system_name}/test-credential", response_model=CredentialTest)
async def test_credential(process_id: str, deployment_id: str, system_name: str, db: DbDep,
                          user: AgentDeployDep) -> CredentialTest:
    """US39.7: fetches a token (or reads the key) without calling the system."""
    del user
    d = _deployment(db, process_id, deployment_id)
    binding = deployments.bindings_of(db, d.id).get(system_name)
    if binding is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"'{system_name}' has no binding")
    contract = next(c for c in AgentDefinition.model_validate(d.snapshot["definition"]).tool_contracts
                    if c.system_name == system_name)
    try:
        await credentials.headers_for(binding.credential, scope_key=binding.id, action=contract.action)
    except credentials.CredentialError as exc:
        return CredentialTest(ok=False, detail=str(exc))
    return CredentialTest(ok=True, detail="Credential works" if binding.credential.get("kind") == "api_key"
                          else "A token was issued")


@router.post("/deployments/{deployment_id}/activate", response_model=Deployment)
def activate_deployment(process_id: str, deployment_id: str, body: ActivateIn, db: DbDep, request: Request,
                        user: AgentApproveDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.activate(db, d, user, shadow_waiver=body.shadow_waiver, rollout=body.rollout, share=body.share)
    except deployments.SegregationError as exc:
        audit.record_refusal(actor=user, action="activate_deployment", reason=str(exc), object_type="deployment",
                             object_id=d.id, process_id=process_id, request=request)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/kill", response_model=StopResult)
def kill_deployment(process_id: str, deployment_id: str, body: StopIn, db: DbDep, user: RuntimeStopDep) -> StopResult:
    d = _deployment(db, process_id, deployment_id)
    try:
        return StopResult(**deployments.kill(db, [d], reason=body.reason, actor=user.id))
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc


@router.post("/agent-artifacts/{artifact_id}/kill", response_model=StopResult)
def kill_agent(process_id: str, artifact_id: str, body: StopIn, db: DbDep, user: RuntimeStopDep) -> StopResult:
    """Every deployment of this agent, whatever its version."""
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    try:
        return StopResult(**deployments.kill(db, deployments.deployments_of_agent(db, process_id, artifact.group_key),
                                             reason=body.reason, actor=user.id))
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc


@router.post("/deployments/{deployment_id}/retire", response_model=Deployment)
def retire_deployment(process_id: str, deployment_id: str, db: DbDep, user: AgentDeployDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.retire(db, d, actor=user.id)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/identity/revoke", response_model=Deployment)
def revoke_identity(process_id: str, deployment_id: str, db: DbDep, user: RuntimeStopDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    deployments.revoke_identity(db, d, user)
    return _detail(db, d)


@router.get("/deployments/{deployment_id}/executions", response_model=list[ExecutionSummary])
def list_executions(process_id: str, deployment_id: str, db: DbDep, user: CurrentUserDep) -> list[ExecutionSummary]:
    del user
    d = _deployment(db, process_id, deployment_id)
    rows = db.scalars(select(AgentExecutionModel).where(AgentExecutionModel.deployment_id == d.id)
                      .order_by(AgentExecutionModel.created_at.desc()).limit(200))
    return [_execution_summary(e) for e in rows]


@router.get("/executions/{execution_id}", response_model=Execution)
def get_execution(process_id: str, execution_id: str, db: DbDep, user: CurrentUserDep) -> Execution:
    del user
    e = db.get(AgentExecutionModel, execution_id)
    if e is None or e.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Execution '{execution_id}' not found")
    return Execution(
        **_execution_summary(e).model_dump(), identity_id=e.identity_id, client_id=e.client_id,
        requested_by=e.requested_by, input=e.input, final_output=e.final_output, deviations=e.deviations,
        resolution=e.resolution, reported_outcome=e.reported_outcome,
        steps=[RunStep.model_validate(s.step) for s in jobs.steps_of(db, e.id)],
    )


# ---- US39.13: rolling out ------------------------------------------------------------


@router.get("/deployments/{deployment_id}/comparison")
def deployment_comparison(process_id: str, deployment_id: str, db: DbDep, user: CurrentUserDep) -> dict:
    """The new version against the one it would replace, shown before each
    step up."""
    del user
    return rollout.comparison(db, _deployment(db, process_id, deployment_id))


@router.post("/deployments/{deployment_id}/promote", response_model=Deployment)
def promote_deployment(process_id: str, deployment_id: str, body: PromoteIn, db: DbDep, request: Request,
                       user: AgentApproveDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    reading = rollout.comparison(db, d)["reading"] if d.status in ("shadow", "active") else None
    try:
        deployments.promote(db, d, user, share=body.share, comparison=reading)
    except deployments.SegregationError as exc:
        audit.record_refusal(actor=user, action="promote_deployment", reason=str(exc), object_type="deployment",
                             object_id=d.id, process_id=process_id, request=request)
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/rollback", response_model=Deployment)
def rollback_deployment(process_id: str, deployment_id: str, body: RollbackIn, db: DbDep,
                        user: RuntimeStopDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.rollback(db, d, user, reason=body.reason)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


# ---- US39.14/US39.15: watching it ------------------------------------------------------


def _window(window: str) -> str:
    if window not in metrics.WINDOWS:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="window must be hour, day or week")
    return window


@router.get("/deployments/{deployment_id}/metrics")
def deployment_metrics(process_id: str, deployment_id: str, db: DbDep, user: CurrentUserDep,
                       window: str = "day") -> dict:
    del user
    return metrics.deployment_metrics(db, _deployment(db, process_id, deployment_id), _window(window))


@router.get("/deployments/{deployment_id}/drift")
def deployment_drift(process_id: str, deployment_id: str, db: DbDep, user: CurrentUserDep) -> dict:
    """Checked now (and remembered, so a flag still notifies or pauses)."""
    del user
    return monitoring.check_drift(db, _deployment(db, process_id, deployment_id), force=True)


@router.post("/deployments/{deployment_id}/drift/to-dataset", response_model=AddedRows)
def drift_to_dataset(process_id: str, deployment_id: str, body: ToDatasetIn, db: DbDep,
                     user: AgentTestDep) -> AddedRows:
    """US39.15/Epic 32: the window's cases become dataset rows."""
    d = _deployment(db, process_id, deployment_id)
    dataset = datasets.get_dataset_model(db, process_id, body.dataset_id)
    existing = {r.row_key for r in datasets.rows_at(db, dataset.id)}
    rows = [r for r in drift.to_dataset_rows(db, d) if r["row_key"] not in existing]
    try:
        if rows:
            datasets.add_rows(db, dataset, [DatasetRowInput.model_validate(r) for r in rows], created_by=user.id)
    except datasets.DatasetError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return AddedRows(added=len(rows))


@router.put("/deployments/{deployment_id}/monitoring", response_model=Deployment)
def update_monitoring(process_id: str, deployment_id: str, body: MonitoringIn, db: DbDep,
                      user: OrgAdminDep) -> Deployment:
    """Alert rules, where traces go, and the drift rule (notify or pause)."""
    d = _deployment(db, process_id, deployment_id)
    before = {"alert_rules": d.alert_rules, "telemetry": d.telemetry, "drift": (d.pause_rules or {}).get("drift")}
    try:
        monitoring.apply_settings(d, alert_rules=body.alert_rules, telemetry_settings=body.telemetry,
                                  drift_rule=body.drift)
    except monitoring.SettingsError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    audit.record(db, actor=user, action="deployment_monitoring_changed", object_type="deployment", object_id=d.id,
                 process_id=process_id, detail={"from": before, "to": body.model_dump(exclude_none=True)})
    db.flush()
    return _detail(db, d)


@router.put("/deployments/{deployment_id}/settings", response_model=Deployment)
def update_settings(process_id: str, deployment_id: str, body: DeploymentSettingsIn, db: DbDep,
                    user: OrgAdminDep) -> Deployment:
    """US39.9/US39.12: an admin adjusts caps, pause rules, the review SLA and
    what new work does while paused. Given keys replace the defaults."""
    d = _deployment(db, process_id, deployment_id)
    before = {"limits": d.limits, "pause_rules": d.pause_rules, "review_sla_minutes": d.review_sla_minutes,
              "when_paused": d.when_paused}
    if body.limits is not None:
        d.limits = {**(d.limits or {}), **body.limits}
    if body.pause_rules is not None:
        d.pause_rules = {**(d.pause_rules or {}), **body.pause_rules}
    if body.review_sla_minutes is not None:
        d.review_sla_minutes = body.review_sla_minutes
    if body.when_paused is not None:
        d.when_paused = body.when_paused
    audit.record(db, actor=user, action="deployment_settings_changed", object_type="deployment", object_id=d.id,
                 process_id=process_id, detail={"from": before, "to": body.model_dump(exclude_none=True)})
    db.flush()
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/pause", response_model=Deployment)
def pause_deployment(process_id: str, deployment_id: str, body: StopIn, db: DbDep, user: RuntimeStopDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.pause(db, d, reason=body.reason, actor=user.name)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


@router.post("/deployments/{deployment_id}/resume", response_model=Deployment)
def resume_deployment(process_id: str, deployment_id: str, body: ResumeIn, db: DbDep,
                      user: RuntimeStopDep) -> Deployment:
    d = _deployment(db, process_id, deployment_id)
    try:
        deployments.resume(db, d, user, comment=body.comment)
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc
    return _detail(db, d)


# ---- the review inbox -----------------------------------------------------------------------


def _task_out(db, task: ReviewTaskModel) -> ReviewTask:
    deployment = db.get(AgentDeploymentModel, task.deployment_id)
    out = ReviewTask.model_validate(task, from_attributes=True)
    out.agent_name = deployment.snapshot["definition"]["name"] if deployment else ""
    out.overdue = task.status == "open" and task.due_at <= utcnow().replace(tzinfo=None)
    return out


def _task(db, process_id: str, task_id: str) -> ReviewTaskModel:
    task = db.get(ReviewTaskModel, task_id)
    if task is None or task.process_id != process_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Review task '{task_id}' not found")
    return task


def _review_refused(exc: reviews.ReviewError, *, user, action: str, task: ReviewTaskModel, request: Request) -> HTTPException:
    if exc.status == 403:
        audit.record_refusal(actor=user, action=action, reason=exc.detail, object_type="review_task",
                             object_id=task.id, process_id=task.process_id, request=request)
    return HTTPException(exc.status, detail=exc.detail)


@router.get("/review-tasks/{task_id}", response_model=ReviewTask)
def get_review_task(process_id: str, task_id: str, db: DbDep, user: CurrentUserDep) -> ReviewTask:
    del user
    return _task_out(db, _task(db, process_id, task_id))


@router.post("/review-tasks/{task_id}/decide", response_model=ReviewTask)
def decide_review_task(process_id: str, task_id: str, body: DecisionIn, db: DbDep, request: Request,
                       user: RuntimeReviewDep) -> ReviewTask:
    """Approve or reject a checkpoint; the case resumes on a worker."""
    task = _task(db, process_id, task_id)
    try:
        reviews.decide(db, task, user, decision=body.decision, comment=body.comment)
    except reviews.ReviewError as exc:
        raise _review_refused(exc, user=user, action="decide_review_task", task=task, request=request) from exc
    execution = db.get(AgentExecutionModel, task.execution_id)
    jobs.queue_resume(db, execution, task_id=task.id, decision=body.decision, note=reviews.note_for(task, user))
    deployments.pause_if_needed(db, db.get(AgentDeploymentModel, task.deployment_id))
    return _task_out(db, task)


@router.post("/review-tasks/{task_id}/resolve", response_model=ReviewTask)
def resolve_review_task(process_id: str, task_id: str, body: ResolutionIn, db: DbDep, request: Request,
                        user: RuntimeReviewDep) -> ReviewTask:
    """Close an escalated case by hand, with the outcome reached."""
    task = _task(db, process_id, task_id)
    try:
        reviews.resolve(db, task, user, outcome=body.outcome, comment=body.comment)
    except reviews.ReviewError as exc:
        raise _review_refused(exc, user=user, action="resolve_review_task", task=task, request=request) from exc
    return _task_out(db, task)


@router.post("/review-tasks/{task_id}/to-dataset", response_model=ReviewTask)
def review_task_to_dataset(process_id: str, task_id: str, body: ToDatasetIn, db: DbDep, request: Request,
                           user: AgentTestDep) -> ReviewTask:
    """US39.10: a person's answer on a live case becomes a golden row."""
    task = _task(db, process_id, task_id)
    try:
        row = reviews.to_dataset_row(task)
    except reviews.ReviewError as exc:
        raise _review_refused(exc, user=user, action="review_task_to_dataset", task=task, request=request) from exc
    dataset = datasets.get_dataset_model(db, process_id, body.dataset_id)
    try:
        datasets.add_rows(db, dataset, [DatasetRowInput.model_validate(row)], created_by=user.id)
    except datasets.DatasetError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return _task_out(db, task)


# ---- the whole tenant -----------------------------------------------------------------


@tenant_router.post("/kill", response_model=StopResult)
def kill_every_agent(body: StopIn, db: DbDep, user: RuntimeStopDep) -> StopResult:
    """US39.11: every agent in the organization. Needs the stop permission
    organization-wide."""
    try:
        return StopResult(**deployments.kill(db, deployments.all_deployments(db), reason=body.reason, actor=user.id))
    except deployments.DeploymentError as exc:
        raise _refused(exc) from exc


@tenant_router.get("/inbox", response_model=list[ReviewTask])
def inbox(db: DbDep, user: CurrentUserDep, include_closed: bool = False) -> list[ReviewTask]:
    """US39.10: the tasks this person may act on, soonest due first."""
    query = select(ReviewTaskModel).order_by(ReviewTaskModel.due_at)
    if not include_closed:
        query = query.where(ReviewTaskModel.status == "open")
    allowed: dict[str, bool] = {}
    out = []
    for task in db.scalars(query.limit(500)):
        if task.process_id not in allowed:
            allowed[task.process_id] = has_permission(db, user, Permission.RUNTIME_REVIEW, process_id=task.process_id)
        if allowed[task.process_id]:
            out.append(_task_out(db, task))
    return out


@tenant_router.get("/deployments", response_model=list[RuntimeOverviewRow])
def runtime_overview(db: DbDep, user: CurrentUserDep, window: str = "day",
                     include_ended: bool = False) -> list[RuntimeOverviewRow]:
    """US39.14: the Runtime page -- each deployment with its numbers over
    the window. Killed and retired ones only if they ran in it."""
    del user
    window = _window(window)
    start = metrics.since(window)
    names = dict(db.execute(select(ProcessModel.id, ProcessModel.name)).all())
    rows = []
    for d in db.scalars(select(AgentDeploymentModel).order_by(AgentDeploymentModel.created_at.desc())):
        if d.status in ("killed", "retired") and not include_ended and not metrics.count_since(db, d.id, start):
            continue
        state = d.monitor_state or {}
        rows.append(RuntimeOverviewRow(
            deployment=RuntimeDeploymentView(**_detail(db, d).model_dump(), process_name=names.get(d.process_id, "")),
            metrics=metrics.deployment_metrics(db, d, window), drift=state.get("drift"),
            alerts=state.get("last_alerts") or [],
        ))
    return rows


@tenant_router.get("/notifications", response_model=list[Notification])
def notifications(db: DbDep, user: CurrentUserDep) -> list[Notification]:
    rows = db.scalars(select(RuntimeNotificationModel).where(RuntimeNotificationModel.user_id == user.id)
                      .order_by(RuntimeNotificationModel.created_at.desc()).limit(100))
    return [Notification.model_validate(r, from_attributes=True) for r in rows]


@tenant_router.get("/clients", response_model=list[RuntimeClient])
def list_clients(db: DbDep, user: OrgAdminDep) -> list[RuntimeClient]:
    del user
    rows = db.scalars(select(RuntimeClientModel).order_by(RuntimeClientModel.created_at))
    return [RuntimeClient.model_validate(r, from_attributes=True) for r in rows]


def _oauth_audience(db, body: RuntimeClientIn, idp: OidcProvider) -> str | None:
    """The audience an OAuth client's tokens must carry; None for a
    bearer-token client. Refuses what can't work."""
    if body.oauth_client_id is None:
        if body.oauth_audience is not None:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail="oauth_audience needs an oauth_client_id")
        return None
    if not idp.config.issuer:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT,
                            detail="This tenant has no identity provider to issue the client's tokens; "
                                   "leave out oauth_client_id to get a bearer token instead")
    taken = db.scalar(select(RuntimeClientModel.id).where(
        RuntimeClientModel.oauth_client_id == body.oauth_client_id, RuntimeClientModel.revoked_at.is_(None)))
    if taken is not None:
        raise HTTPException(status.HTTP_409_CONFLICT,
                            detail=f"OAuth client '{body.oauth_client_id}' is already registered ({taken})")
    return body.oauth_audience or get_settings().runtime_oauth_audience


@tenant_router.post("/clients", response_model=RuntimeClientCreated, status_code=201)
def create_client(body: RuntimeClientIn, db: DbDep, user: OrgAdminDep,
                  idp: Annotated[OidcProvider, Depends(get_oidc_provider)]) -> RuntimeClientCreated:
    """With `oauth_client_id`, the system signs in through the tenant's
    IdP and no token is issued here. Without, it gets a bearer token. The
    token and the callback signing secret are shown once; only the token's
    hash is kept."""
    unknown = [d for d in body.deployment_ids or [] if db.get(AgentDeploymentModel, d) is None]
    if unknown:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"Unknown deployments: {', '.join(unknown)}")
    audience = _oauth_audience(db, body, idp)
    token = None if body.oauth_client_id is not None else "axr_" + secrets.token_urlsafe(32)
    signing_secret = "axs_" + secrets.token_urlsafe(32)
    row = RuntimeClientModel(id=new_id("rtc"), label=body.label, token_hash=_hash(token) if token else None,
                             oauth_client_id=body.oauth_client_id, oauth_audience=audience, created_by=user.id,
                             deployment_ids=body.deployment_ids, signing_secret=signing_secret)
    db.add(row)
    db.flush()
    db.refresh(row)
    return RuntimeClientCreated(**RuntimeClient.model_validate(row, from_attributes=True).model_dump(), token=token,
                                signing_secret=signing_secret)


@tenant_router.post("/clients/{client_id}/revoke", response_model=RuntimeClient)
def revoke_client(client_id: str, db: DbDep, user: OrgAdminDep) -> RuntimeClient:
    del user
    row = db.get(RuntimeClientModel, client_id)
    if row is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Client '{client_id}' not found")
    row.revoked_at = row.revoked_at or utcnow()
    db.flush()
    return RuntimeClient.model_validate(row, from_attributes=True)


# ---- for the tenant's systems ------------------------------------------------------------


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _unauthorized(reason: str | None = None) -> HTTPException:
    challenge = 'Bearer realm="runtime"' + (', error="invalid_token"' if reason else "")
    detail = "A valid runtime client token is required" + (f": {reason}" if reason else "")
    return HTTPException(status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": challenge})


def _oauth_client(db, provider: OidcProvider, token: str) -> RuntimeClientModel:
    """A client-credentials access token from the tenant's own IdP. The
    issuer is the tenant's, so another tenant's IdP can't vouch for a
    system here; the client it names must be registered and unrevoked."""
    # The tenant's IdP, whether or not its people sign in through it yet.
    if not provider.config.issuer:
        raise _unauthorized()
    clients: dict[str, RuntimeClientModel] = {}

    def audience_for(client_id: str) -> str | None:
        row = db.scalar(select(RuntimeClientModel).where(RuntimeClientModel.oauth_client_id == client_id,
                                                         RuntimeClientModel.revoked_at.is_(None)))
        if row is None:
            return None
        clients[client_id] = row
        return row.oauth_audience or get_settings().runtime_oauth_audience

    try:
        client_id, _ = provider.validate_access_token(token, audience_for=audience_for)
    except OidcError as exc:
        raise _unauthorized(str(exc)) from exc
    except IdpUnavailable as exc:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail="The identity provider can't be reached to check the token") from exc
    return clients[client_id]


def runtime_client(db: DbDep, provider: Annotated[OidcProvider, Depends(get_oidc_provider)],
                   authorization: Annotated[str | None, Header()] = None) -> RuntimeClientModel:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise _unauthorized()
    if token.startswith("axr_"):
        row = db.scalar(select(RuntimeClientModel).where(RuntimeClientModel.token_hash == _hash(token)))
        if row is None or row.revoked_at is not None:
            raise _unauthorized()
    else:
        row = _oauth_client(db, provider, token)
    now = utcnow().replace(tzinfo=None)
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
    return row


ClientDep = Annotated[RuntimeClientModel, Depends(runtime_client)]


def _accepted(e: AgentExecutionModel) -> ExecutionAccepted:
    return ExecutionAccepted(id=e.id, status=e.status, final_output=e.final_output, deviations=e.deviations)


@client_router.post("/deployments/{deployment_id}/executions", response_model=ExecutionAccepted, status_code=202)
def start_execution(deployment_id: str, body: ExecutionIn, db: DbDep, caller: ClientDep, response: Response,
                    idempotency_key: Annotated[str | None, Header()] = None) -> ExecutionAccepted:
    deployment = db.get(AgentDeploymentModel, deployment_id)
    if deployment is None or (caller.deployment_ids is not None and deployment_id not in caller.deployment_ids):
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Deployment '{deployment_id}' not found")
    if body.callback_url:
        try:
            check_production_url(body.callback_url)
        except UnsafeProductionEndpoint as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"callback_url: {exc}") from exc
    try:
        execution, created = jobs.submit(db, deployment, jobs.Caller(client_id=caller.id, label=caller.label),
                                         data=body.input,
                                         idempotency_key=idempotency_key or "", callback_url=body.callback_url,
                                         requested_by=body.requested_by)
    except jobs.SubmitError as exc:
        raise HTTPException(exc.status, detail=exc.detail) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return _accepted(execution)


@client_router.post("/executions/{execution_id}/outcome", response_model=ExecutionAccepted)
def report_outcome(execution_id: str, body: OutcomeIn, db: DbDep, caller: ClientDep) -> ExecutionAccepted:
    """US39.13/US39.15: what really happened to the case. It is what a
    shadow version is checked against, and live accuracy for drift."""
    e = db.get(AgentExecutionModel, execution_id)
    if e is None or e.client_id != caller.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Execution '{execution_id}' not found")
    e.reported_outcome = body.outcome
    e.reported_at = utcnow()
    db.flush()
    return _accepted(e)


@client_router.get("/executions/{execution_id}", response_model=ExecutionAccepted)
def read_execution(execution_id: str, db: DbDep, caller: ClientDep) -> ExecutionAccepted:
    e = db.get(AgentExecutionModel, execution_id)
    if e is None or e.client_id != caller.id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Execution '{execution_id}' not found")
    return _accepted(e)

