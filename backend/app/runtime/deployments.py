"""Epic 39, US39.2, US39.5-39.7, US39.11: deployments, their identities and
bindings, and stopping them.

A deployment pins one approved artifact version (its definition and the
tool schemas the twin tested) and goes through:

    pending --activate--> active --kill--> killed
                                 --retire--> retired

Creating one needs `agent.deploy`; activating it needs `agent.approve`,
held by someone who neither built the agent nor asked for the deployment
(Epic 37's rule). A killed deployment never comes back: a new one is
created and approved instead. Everything here fails closed: an agent
without tool contracts, a contract without a binding or a credential, an
untested tool shape, a revoked identity -- each blocks activation.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import secret_store
from app.db.models import (
    AdapterBindingModel,
    AgentArtifactModel,
    AgentDeploymentModel,
    AgentExecutionModel,
    AgentIdentityModel,
    ContextEntityModel,
    ContributionModel,
    JobModel,
    TwinToolSchemaModel,
    UserModel,
)
from app.ids import new_id, utcnow
from app.schemas.agents import AgentDefinition
from app.schemas.run import NON_TERMINAL_RUN_STATUSES
from app.tenancy.identity import current_slug

from . import credentials
from .adapters import UnsafeProductionEndpoint, check_production_endpoint, is_mutating

LIVE = ("active", "paused")


class DeploymentError(Exception):
    """Refused, with every reason listed."""

    def __init__(self, problems: list[str] | str):
        self.problems = [problems] if isinstance(problems, str) else problems
        super().__init__("; ".join(self.problems))


class SegregationError(Exception):
    pass


# ---- creating -------------------------------------------------------------------------


def _tested_schemas(session: Session, artifact: AgentArtifactModel, definition: AgentDefinition) -> dict[str, dict[str, Any]]:
    """The tools the twin offers this agent: built from each linked
    application's OpenAPI spec (Epic 29), as the twin builds them for every
    run, else the inferred ones it cached. Runs don't record which spec
    they used, so the spec as it is now is pinned, and a later change to
    it shows up as a difference on the binding."""
    from app.catalog.service import spec_tool_schemas

    row = session.get(TwinToolSchemaModel, artifact.id)
    schemas = dict(row.schemas) if row is not None else {}
    for name, schema in spec_tool_schemas(session, artifact.process_id, definition.tools_systems_needed).items():
        schemas[name] = schema.model_dump(mode="json")
    return schemas


def _creation_problems(definition: AgentDefinition, schemas: dict[str, dict[str, Any]], approved: bool) -> list[str]:
    problems = []
    if not approved:
        problems.append("approve this agent version before deploying it")
    if not definition.tool_contracts:
        problems.append("the agent declares no tool contracts, so nothing would limit what it may call")
    for contract in definition.tool_contracts:
        schema = schemas.get(contract.system_name) or {}
        if schema.get("source") != "openapi" or not schema.get("api_operations"):
            problems.append(
                f"'{contract.system_name}' was tested with a simulated tool, not its API: link it to a catalog "
                "application with an OpenAPI spec and test the agent again"
            )
    return problems


def _production_endpoints(app: ContextEntityModel | None) -> list[str]:
    if app is None:
        return []
    return [e.get("url", "") for e in (app.data or {}).get("endpoints") or [] if e.get("environment") == "production"]


def create(session: Session, artifact: AgentArtifactModel, user: UserModel) -> AgentDeploymentModel:
    definition = AgentDefinition.model_validate(artifact.definition)
    schemas = _tested_schemas(session, artifact, definition)
    problems = _creation_problems(definition, schemas, artifact.approved_at is not None)
    if problems:
        raise DeploymentError(problems)
    identity = AgentIdentityModel(
        id=new_id("agid"), display_name=f"{definition.name} v{artifact.version}", status="active",
    )
    session.add(identity)
    session.flush()
    deployment = AgentDeploymentModel(
        id=new_id("dep"),
        process_id=artifact.process_id,
        agent_artifact_id=artifact.id,
        group_key=artifact.group_key,
        artifact_version=artifact.version,
        snapshot={
            "definition": definition.model_dump(mode="json"),
            "tool_schemas": {c.system_name: schemas[c.system_name] for c in definition.tool_contracts},
            "approved_at": artifact.approved_at.isoformat() if artifact.approved_at else None,
            "approved_by": artifact.approved_by,
            "context_pack_id": artifact.context_pack_id,
        },
        environment="production",
        status="pending",
        identity_id=identity.id,
        created_by=user.id,
    )
    session.add(deployment)
    session.flush()
    # Prefill each binding with the application's production endpoint and
    # every tested operation; the architect narrows it and adds a credential.
    for contract in definition.tool_contracts:
        app = session.get(ContextEntityModel, contract.application_id) if contract.application_id else None
        endpoints = _production_endpoints(app)
        if endpoints:
            session.add(AdapterBindingModel(
                id=new_id("bind"), deployment_id=deployment.id, system_name=contract.system_name,
                application_id=app.id, base_url=endpoints[0],
                operations=sorted(deployment.snapshot["tool_schemas"][contract.system_name]["api_operations"]),
                credential={}, accepted_differences=[], updated_by=user.id,
            ))
    session.flush()
    return deployment


# ---- bindings -------------------------------------------------------------------------


@dataclass
class BindingInput:
    base_url: str
    operations: list[str]
    credential: dict[str, Any]
    accept_differences: list[str]


def _contract(deployment: AgentDeploymentModel, system_name: str):
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    contract = next((c for c in definition.tool_contracts if c.system_name == system_name), None)
    if contract is None:
        raise DeploymentError(f"'{system_name}' isn't one of this agent's tool contracts")
    return contract


def _check_binding(deployment: AgentDeploymentModel, system_name: str, body: BindingInput,
                   app: ContextEntityModel | None) -> None:
    if body.base_url not in _production_endpoints(app):
        raise DeploymentError(
            f"'{body.base_url}' isn't a production endpoint registered for '{system_name}' in the application catalog"
        )
    try:
        check_production_endpoint(body.base_url, "production")
    except UnsafeProductionEndpoint as exc:
        raise DeploymentError(str(exc)) from exc
    tested = deployment.snapshot["tool_schemas"][system_name]["api_operations"]
    untested = sorted(set(body.operations) - set(tested))
    if untested:
        raise DeploymentError(f"allow only operations the twin tested; not tested: {untested}")
    if not body.operations:
        raise DeploymentError("allow at least one operation")
    found = credentials.problems(body.credential)
    if found:
        raise DeploymentError(found)


def set_binding(session: Session, deployment: AgentDeploymentModel, system_name: str, body: BindingInput,
                user: UserModel) -> AdapterBindingModel:
    if deployment.status != "pending":
        raise DeploymentError("bindings can only change before activation; deploy a new version to change them")
    contract = _contract(deployment, system_name)
    app = session.get(ContextEntityModel, contract.application_id) if contract.application_id else None
    _check_binding(deployment, system_name, body, app)
    binding = session.scalar(select(AdapterBindingModel).where(
        AdapterBindingModel.deployment_id == deployment.id, AdapterBindingModel.system_name == system_name))
    if binding is None:
        binding = AdapterBindingModel(id=new_id("bind"), deployment_id=deployment.id, system_name=system_name)
        session.add(binding)
    binding.application_id = app.id if app is not None else None
    binding.base_url = body.base_url
    binding.operations = sorted(set(body.operations))
    binding.credential = body.credential
    binding.accepted_differences = sorted(set(body.accept_differences))
    binding.updated_by = user.id
    session.flush()
    return binding


def bindings_of(session: Session, deployment_id: str) -> dict[str, AdapterBindingModel]:
    rows = session.scalars(select(AdapterBindingModel).where(AdapterBindingModel.deployment_id == deployment_id))
    return {row.system_name: row for row in rows}


def operation_differences(session: Session, deployment: AgentDeploymentModel, binding: AdapterBindingModel) -> list[str]:
    """Allowed operations whose method or path in the application's current
    spec differ from what the twin tested."""
    app = session.get(ContextEntityModel, binding.application_id) if binding.application_id else None
    current = {op.get("operation_id"): op for op in ((app.data or {}).get("api_operations") or [] if app else [])}
    tested = deployment.snapshot["tool_schemas"][binding.system_name]["api_operations"]
    differences = []
    for op_id in binding.operations:
        now = current.get(op_id)
        if now is None:
            differences.append(f"{op_id}: no longer in the application's spec")
        elif (now.get("method"), now.get("path")) != (tested[op_id]["method"], tested[op_id]["path"]):
            differences.append(f"{op_id}: tested {tested[op_id]['method']} {tested[op_id]['path']}, "
                               f"now {now.get('method')} {now.get('path')}")
    return differences


def readiness(session: Session, deployment: AgentDeploymentModel) -> list[str]:
    """Everything that stops this deployment from going active."""
    problems = []
    definition = AgentDefinition.model_validate(deployment.snapshot["definition"])
    identity = session.get(AgentIdentityModel, deployment.identity_id)
    if identity is None or identity.status != "active":
        problems.append("the agent's identity is revoked")
    bindings = bindings_of(session, deployment.id)
    for contract in definition.tool_contracts:
        binding = bindings.get(contract.system_name)
        if binding is None:
            problems.append(f"'{contract.system_name}' has no production binding (register a production endpoint "
                            "for its application in the catalog)")
            continue
        problems += [f"'{contract.system_name}' {p}" for p in _binding_problems(session, deployment, binding)]
        if is_mutating(contract) and not deployment.shadow_waiver:
            problems.append(f"'{contract.system_name}' changes data, so the agent runs in shadow first (US39.13, "
                            "not built yet) or an approver waives that with a reason")
    return problems


def _binding_problems(session: Session, deployment: AgentDeploymentModel, binding: AdapterBindingModel) -> list[str]:
    found = [f"credential: {problem}" for problem in credentials.problems(binding.credential)]
    ref = binding.credential.get("secret_ref")
    if ref and not found and not secret_store.resolve(ref, current_slug()):
        found.append(f"credential: the secret isn't set ({secret_store.where_to_put(ref, current_slug() or '')})")
    found += [f"changed since it was tested: {d}" for d in operation_differences(session, deployment, binding)
              if d not in binding.accepted_differences]
    return found


# ---- activating ------------------------------------------------------------------------


def builders(session: Session, artifact_id: str) -> set[str]:
    """Who built or amended this agent's definition: they can't approve
    running it."""
    artifact = session.get(AgentArtifactModel, artifact_id)
    actors = set(session.scalars(select(ContributionModel.actor_id).where(
        ContributionModel.object_type == "agent", ContributionModel.object_id == artifact_id,
        ContributionModel.tier == "high")))
    if artifact is not None and artifact.generated_by:
        actors.add(artifact.generated_by)
    return {a for a in actors if a}


def activate(session: Session, deployment: AgentDeploymentModel, user: UserModel, *,
             shadow_waiver: str | None) -> AgentDeploymentModel:
    if deployment.status != "pending":
        raise DeploymentError(f"only a pending deployment can be activated; this one is {deployment.status}")
    if user.id == deployment.created_by:
        raise SegregationError("You asked for this deployment -- someone else must approve it")
    if user.id in builders(session, deployment.agent_artifact_id):
        raise SegregationError("You built or amended this agent -- someone else must approve running it")
    if shadow_waiver is not None:
        deployment.shadow_waiver = shadow_waiver.strip() or None
    problems = readiness(session, deployment)
    if problems:
        raise DeploymentError(problems)
    # Activating a new version retires the one it replaces.
    for other in session.scalars(select(AgentDeploymentModel).where(
            AgentDeploymentModel.group_key == deployment.group_key, AgentDeploymentModel.process_id == deployment.process_id,
            AgentDeploymentModel.status.in_(LIVE), AgentDeploymentModel.id != deployment.id)):
        _stop(session, other, status="retired", reason=f"Replaced by version {deployment.artifact_version}",
              actor=user.id)
    deployment.status = "active"
    deployment.approved_by = user.id
    deployment.activated_at = utcnow()
    session.flush()
    return deployment


# ---- stopping -------------------------------------------------------------------------


def _stop(session: Session, deployment: AgentDeploymentModel, *, status: str, reason: str, actor: str) -> int:
    """Stops one deployment. Executions not started yet end KILLED at once;
    running ones see the new status before their next step (app/runtime/
    jobs.py) and stop there. Returns how many were waiting."""
    deployment.status = status
    deployment.stopped_at = utcnow()
    deployment.stopped_by = actor
    deployment.stop_reason = reason
    waiting = list(session.scalars(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.status == "QUEUED")))
    for execution in waiting:
        execution.status = "KILLED"
        execution.completed_at = utcnow()
        execution.deviations = [{"reason": f"Stopped before it started: {reason}", "step_index": None}]
        job = session.scalar(select(JobModel).where(JobModel.dedupe_key == f"runtime:{execution.id}"))
        if job is not None and job.status == "queued":
            job.status = "cancelled"
            job.finished_at = utcnow()
    session.flush()
    return len(waiting)


def kill(session: Session, deployments: list[AgentDeploymentModel], *, reason: str, actor: str) -> dict[str, int]:
    reason = (reason or "").strip()
    if not reason:
        raise DeploymentError("give a reason for stopping the agent")
    live = [d for d in deployments if d.status in (*LIVE, "pending")]
    waiting = sum(_stop(session, d, status="killed", reason=reason, actor=actor) for d in live)
    running = session.scalar(select(AgentExecutionModel.id).where(
        AgentExecutionModel.deployment_id.in_([d.id for d in live]),
        AgentExecutionModel.status.in_(NON_TERMINAL_RUN_STATUSES)).limit(1)) if live else None
    return {"deployments": len(live), "waiting_stopped": waiting, "running": int(running is not None)}


def deployments_of_agent(session: Session, process_id: str, group_key: str) -> list[AgentDeploymentModel]:
    return list(session.scalars(select(AgentDeploymentModel).where(
        AgentDeploymentModel.process_id == process_id, AgentDeploymentModel.group_key == group_key)))


def all_deployments(session: Session) -> list[AgentDeploymentModel]:
    return list(session.scalars(select(AgentDeploymentModel)))


def retire(session: Session, deployment: AgentDeploymentModel, *, actor: str) -> None:
    if deployment.status not in (*LIVE, "pending"):
        raise DeploymentError(f"this deployment is already {deployment.status}")
    _stop(session, deployment, status="retired", reason="Retired", actor=actor)


def revoke_identity(session: Session, deployment: AgentDeploymentModel, user: UserModel) -> None:
    """US39.5: the agent can no longer act. Same effect on its work as a kill."""
    identity = session.get(AgentIdentityModel, deployment.identity_id)
    if identity is not None and identity.status == "active":
        identity.status = "revoked"
        identity.revoked_at = utcnow()
        identity.revoked_by = user.id
    credentials.forget({b.id for b in bindings_of(session, deployment.id).values()})
    if deployment.status in (*LIVE, "pending"):
        _stop(session, deployment, status="killed", reason="The agent's identity was revoked", actor=user.id)
    session.flush()
