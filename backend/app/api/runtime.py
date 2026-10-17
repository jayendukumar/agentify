"""Epic 39: the agent runtime's routes.

Two audiences:

- **People** (`/api/...`, session and permission guarded): deploy an
  approved agent version, bind its tools, approve and activate it, stop
  it, and read its executions. Stopping one agent, every deployment of an
  agent, or every agent in the tenant is one action each (US39.11).
- **The tenant's own systems** (`/runtime/v1/...`): start an execution
  and read its result, with a runtime client's bearer token (US39.4),
  the way the IdP reaches `/scim/v2`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Request, Response, status
from sqlalchemy import select

from app.auth import audit
from app.auth.permissions import Permission, has_permission
from app.db import repository
from app.db.models import (
    AgentDeploymentModel,
    AgentExecutionModel,
    AgentIdentityModel,
    ReviewTaskModel,
    RuntimeClientModel,
    RuntimeNotificationModel,
)
from app.ids import new_id, utcnow
from app.datasets import service as datasets
from app.runtime import credentials, deployments, jobs, limits, reviews
from app.runtime.adapters import UnsafeProductionEndpoint, check_production_url
from app.schemas.agents import AgentDefinition
from app.schemas.datasets import DatasetRowInput
from app.schemas.run import RunStep
from app.schemas.runtime import (
    ActivateIn,
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
        executions=jobs.counts_by_status(db, d.id),
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
    )


def _execution_summary(e: AgentExecutionModel) -> ExecutionSummary:
    return ExecutionSummary(
        id=e.id, deployment_id=e.deployment_id, artifact_version=e.artifact_version, status=e.status,
        attempt=e.attempt, step_count=e.step_count, total_cost_usd=e.total_cost_usd, total_tokens=e.total_tokens,
        created_at=e.created_at, completed_at=e.completed_at,
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
        deployments.activate(db, d, user, shadow_waiver=body.shadow_waiver)
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
        resolution=e.resolution,
        steps=[RunStep.model_validate(s.step) for s in jobs.steps_of(db, e.id)],
    )


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


@tenant_router.post("/clients", response_model=RuntimeClientCreated, status_code=201)
def create_client(body: RuntimeClientIn, db: DbDep, user: OrgAdminDep) -> RuntimeClientCreated:
    """The token is shown once; only its hash is kept."""
    token = "axr_" + secrets.token_urlsafe(32)
    row = RuntimeClientModel(id=new_id("rtc"), label=body.label, token_hash=_hash(token), created_by=user.id)
    db.add(row)
    db.flush()
    db.refresh(row)
    return RuntimeClientCreated(**RuntimeClient.model_validate(row, from_attributes=True).model_dump(), token=token)


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


def runtime_client(db: DbDep, authorization: Annotated[str | None, Header()] = None) -> jobs.Caller:
    scheme, _, token = (authorization or "").partition(" ")
    row = None
    if scheme.lower() == "bearer" and token:
        row = db.scalar(select(RuntimeClientModel).where(RuntimeClientModel.token_hash == _hash(token)))
    if row is None or row.revoked_at is not None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="A valid runtime client token is required",
                            headers={"WWW-Authenticate": 'Bearer realm="runtime"'})
    now = utcnow().replace(tzinfo=None)
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
    return jobs.Caller(client_id=row.id, label=row.label)


ClientDep = Annotated[jobs.Caller, Depends(runtime_client)]


def _accepted(e: AgentExecutionModel) -> ExecutionAccepted:
    return ExecutionAccepted(id=e.id, status=e.status, final_output=e.final_output, deviations=e.deviations)


@client_router.post("/deployments/{deployment_id}/executions", response_model=ExecutionAccepted, status_code=202)
def start_execution(deployment_id: str, body: ExecutionIn, db: DbDep, caller: ClientDep, response: Response,
                    idempotency_key: Annotated[str | None, Header()] = None) -> ExecutionAccepted:
    deployment = db.get(AgentDeploymentModel, deployment_id)
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Deployment '{deployment_id}' not found")
    if body.callback_url:
        try:
            check_production_url(body.callback_url)
        except UnsafeProductionEndpoint as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"callback_url: {exc}") from exc
    try:
        execution, created = jobs.submit(db, deployment, caller, data=body.input,
                                         idempotency_key=idempotency_key or "", callback_url=body.callback_url,
                                         requested_by=body.requested_by)
    except jobs.SubmitError as exc:
        raise HTTPException(exc.status, detail=exc.detail) from exc
    if not created:
        response.status_code = status.HTTP_200_OK
    return _accepted(execution)


@client_router.get("/executions/{execution_id}", response_model=ExecutionAccepted)
def read_execution(execution_id: str, db: DbDep, caller: ClientDep) -> ExecutionAccepted:
    e = db.get(AgentExecutionModel, execution_id)
    if e is None or e.client_id != caller.client_id:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Execution '{execution_id}' not found")
    return _accepted(e)

