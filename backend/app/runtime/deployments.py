"""Epic 39, US39.2, US39.5-39.7, US39.11: deployments, their identities and
bindings, and stopping them.

A deployment pins one approved artifact version (its definition and the
tool schemas the twin tested) and goes through:

    pending --activate--> active --kill--> killed
           (or shadow)           --retire--> retired
                                 --pause--> paused --resume--> active
    shadow --promote--> active (a canary, or all cases)
    canary/shadow --rollback--> retired
    pending --hand off--> external (runs on the customer's platform, US39.18)

Paused (US39.12, by hand or by a pause rule) is a halt, not a stop:
running cases finish, new ones are refused or queued, and resuming needs
no new approval. Retired takes no new cases either; the ones it already
accepted finish on it. Killed stops them at their next step.

US39.13, rolling out a new version of an agent that already runs:

- **Shadow:** the new version gets a copy of every live case. Its reads go
  to the real systems; calls that would change data are recorded and not
  sent. Required first for an agent with a data-changing tool unless an
  approver waives it. A first version in shadow, with nothing live yet,
  takes the cases sent to it directly, and the caller reports what the
  people actually did.
- **Canary:** an active new version takes `traffic_share` percent of the
  agent's new cases, chosen by a hash of the idempotency key, so a case
  always lands on the same version. A caller keeps using whichever
  deployment id it has; the case is routed within the agent.
- **Promote** steps the share up (5, 25, 100); at 100 the previous version
  retires. **Rollback** retires the canary or shadow: new cases go back to
  the previous version, and cases already running finish where they are.

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
from app.auth import audit
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

from . import callbacks, credentials, drift, limits, reviews
from .adapters import UnsafeProductionEndpoint, check_production_endpoint, is_mutating

LIVE = ("active", "paused")
# Statuses a running case may carry on under (the kill switch stops the rest).
CARRY_ON = (*LIVE, "shadow", "retired")


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
    # US39.9/US39.12: caps and pause rules from the twin's evidence.
    evidence = limits.twin_evidence(session, artifact.id, definition)
    deployment.limits = limits.default_limits(session, artifact.process_id, evidence)
    deployment.pause_rules = limits.default_pause_rules(evidence)
    # US39.15: what the twin measured on this version, frozen for drift.
    deployment.snapshot = {**deployment.snapshot, "twin_evidence": evidence,
                           "baseline": drift.baseline(session, artifact.id, artifact.version,
                                                      [f.name for f in definition.input_schema])}
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


def readiness(session: Session, deployment: AgentDeploymentModel, *, shadow_ok: bool = False) -> list[str]:
    """Everything that stops this deployment from going active (or, with
    `shadow_ok`, into shadow, where nothing that changes data is sent)."""
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
        if is_mutating(contract) and not (shadow_ok or deployment.shadow_waiver or deployment.shadow_started_at):
            problems.append(f"'{contract.system_name}' changes data, so the agent runs in shadow first (activate it "
                            "with rollout 'shadow') or an approver waives that with a reason")
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


def check_segregation(session: Session, deployment: AgentDeploymentModel, user: UserModel) -> None:
    if user.id == deployment.created_by:
        raise SegregationError("You asked for this deployment -- someone else must approve it")
    if user.id in builders(session, deployment.agent_artifact_id):
        raise SegregationError("You built or amended this agent -- someone else must approve running it")


def siblings(session: Session, deployment: AgentDeploymentModel, statuses: tuple[str, ...]) -> list[AgentDeploymentModel]:
    """Other deployments of the same agent in these statuses, oldest first."""
    return list(session.scalars(select(AgentDeploymentModel).where(
        AgentDeploymentModel.group_key == deployment.group_key, AgentDeploymentModel.process_id == deployment.process_id,
        AgentDeploymentModel.status.in_(statuses), AgentDeploymentModel.id != deployment.id)
        .order_by(AgentDeploymentModel.created_at)))


def _check_share(share: int | None) -> int:
    if share is None or not 1 <= share <= 99:
        raise DeploymentError("a canary takes between 1 and 99 percent of cases")
    return share


def _replace(session: Session, deployment: AgentDeploymentModel, current: list[AgentDeploymentModel], actor: str) -> None:
    """The new version takes every case; the ones it replaces retire."""
    for other in current:
        _stop(session, other, status="retired", reason=f"Replaced by version {deployment.artifact_version}", actor=actor)
    deployment.status = "active"
    deployment.traffic_share = None


def _start_rollout(session: Session, deployment: AgentDeploymentModel, rollout: str, share: int | None,
                   current: list[AgentDeploymentModel], actor: str) -> None:
    if rollout == "shadow":
        if siblings(session, deployment, ("shadow",)):
            raise DeploymentError("another version of this agent is already in shadow; promote or roll it back first")
        deployment.status = "shadow"
        deployment.shadow_started_at = utcnow()
    elif rollout == "canary":
        if not current:
            raise DeploymentError("nothing runs yet to compare a canary with: activate it for all cases")
        _no_other_canary(current)
        deployment.status = "active"
        deployment.traffic_share = _check_share(share)
    else:
        _replace(session, deployment, current, actor)


def _no_other_canary(current: list[AgentDeploymentModel]) -> None:
    if any(c.traffic_share for c in current):
        raise DeploymentError("a canary of this agent is already running; promote or roll it back first")


def activate(session: Session, deployment: AgentDeploymentModel, user: UserModel, *,
             shadow_waiver: str | None, rollout: str = "full", share: int | None = None) -> AgentDeploymentModel:
    """`rollout`: "full" (all of the agent's cases; the live version
    retires), "canary" (`share` percent, next to the live version) or
    "shadow" (a copy of live cases, nothing that changes data sent)."""
    if deployment.status != "pending":
        raise DeploymentError(f"only a pending deployment can be activated; this one is {deployment.status}")
    check_segregation(session, deployment, user)
    if shadow_waiver is not None:
        deployment.shadow_waiver = shadow_waiver.strip() or None
    problems = readiness(session, deployment, shadow_ok=rollout == "shadow")
    if problems:
        raise DeploymentError(problems)
    current = siblings(session, deployment, LIVE)
    deployment.previous_deployment_id = current[-1].id if current else None
    _start_rollout(session, deployment, rollout, share, current, user.id)
    deployment.approved_by = user.id
    deployment.activated_at = utcnow() if deployment.status == "active" else None
    audit.record(session, actor=user, action="deployment_activated", object_type="deployment", object_id=deployment.id,
                 process_id=deployment.process_id,
                 detail={"rollout": rollout, "share": deployment.traffic_share,
                         "replaces": deployment.previous_deployment_id, "shadow_waiver": deployment.shadow_waiver})
    session.flush()
    return deployment


def _check_promotable(session: Session, deployment: AgentDeploymentModel, user: UserModel, share: int) -> None:
    canary = deployment.status in LIVE and bool(deployment.traffic_share)
    if deployment.status != "shadow" and not canary:
        takes_all = " and takes every case" if deployment.status in LIVE else ""
        raise DeploymentError(f"only a version in shadow or a canary can be promoted; this one is "
                              f"{deployment.status}{takes_all}")
    check_segregation(session, deployment, user)
    if not 1 <= share <= 100:
        raise DeploymentError("give a share between 1 and 100 percent")
    problems = readiness(session, deployment) if deployment.status == "shadow" else []
    if problems:
        raise DeploymentError(problems)


def promote(session: Session, deployment: AgentDeploymentModel, user: UserModel, *, share: int,
            comparison: str | None = None) -> AgentDeploymentModel:
    """US39.13: a shadow version starts taking cases, or a canary takes a
    bigger share; at 100 it takes them all and the previous version
    retires. Approved like an activation (not by who built or asked)."""
    _check_promotable(session, deployment, user, share)
    before = "shadow" if deployment.status == "shadow" else f"{deployment.traffic_share}%"
    current = siblings(session, deployment, LIVE)
    if share == 100 or not current:
        _replace(session, deployment, current, user.id)
    else:
        if deployment.status == "shadow":
            _no_other_canary(current)
            deployment.status = "active"
        deployment.traffic_share = share
    deployment.activated_at = deployment.activated_at or utcnow()
    audit.record(session, actor=user, action="deployment_promoted", object_type="deployment", object_id=deployment.id,
                 process_id=deployment.process_id,
                 detail={"from": before, "to": f"{deployment.traffic_share or 100}%", "comparison": comparison})
    session.flush()
    return deployment


def rollback(session: Session, deployment: AgentDeploymentModel, user: UserModel, *, reason: str) -> None:
    """US39.13: new cases go back to the previous version at once; cases
    already running on this one finish on it."""
    reason = (reason or "").strip()
    if not reason:
        raise DeploymentError("say why you're rolling back")
    if deployment.status != "shadow" and not (deployment.status in LIVE and deployment.traffic_share):
        raise DeploymentError("only a canary or a version in shadow can be rolled back; stop or retire this one instead")
    _stop(session, deployment, status="retired", reason=f"Rolled back: {reason}", actor=user.id)
    audit.record(session, actor=user, action="deployment_rolled_back", object_type="deployment",
                 object_id=deployment.id, process_id=deployment.process_id,
                 detail={"reason": reason, "back_to": deployment.previous_deployment_id})
    session.flush()


# ---- stopping -------------------------------------------------------------------------


def _stop(session: Session, deployment: AgentDeploymentModel, *, status: str, reason: str, actor: str) -> int:
    """Stops one deployment. Killed: executions not started yet end KILLED
    at once; running ones see the new status before their next step
    (app/runtime/jobs.py) and stop there. Retired: it takes no new cases,
    and the ones it accepted finish. Returns how many were stopped waiting."""
    deployment.status = status
    deployment.stopped_at = utcnow()
    deployment.stopped_by = actor
    deployment.stop_reason = reason
    deployment.traffic_share = None
    if status == "retired":
        session.flush()
        return 0
    waiting = list(session.scalars(select(AgentExecutionModel).where(
        AgentExecutionModel.deployment_id == deployment.id, AgentExecutionModel.status == "QUEUED")))
    for execution in waiting:
        execution.status = "KILLED"
        execution.completed_at = utcnow()
        execution.deviations = [{"reason": f"Stopped before it started: {reason}", "step_index": None}]
        callbacks.notify_caller(session, execution)
        for job in session.scalars(select(JobModel).where(JobModel.dedupe_key.like(f"runtime:{execution.id}%"),
                                                         JobModel.status == "queued")):
            job.status = "cancelled"
            job.finished_at = utcnow()
    reviews.cancel_open(session, deployment.id, f"The deployment was {status}: {reason}")
    session.flush()
    return len(waiting)


def kill(session: Session, deployments: list[AgentDeploymentModel], *, reason: str, actor: str) -> dict[str, int]:
    reason = (reason or "").strip()
    if not reason:
        raise DeploymentError("give a reason for stopping the agent")
    live = [d for d in deployments if d.status in (*LIVE, "pending", "shadow", "external")]
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


def pause(session: Session, deployment: AgentDeploymentModel, *, reason: str, actor: str) -> None:
    """US39.12: new work is refused or queued (`when_paused`); running cases
    finish. Owners are told why. Undone by `resume`, with no new approval."""
    if deployment.status != "active":
        raise DeploymentError(f"only an active deployment can be paused; this one is {deployment.status}")
    deployment.status = "paused"
    deployment.paused_at = utcnow()
    deployment.pause_reason = reason
    reviews.notify(session, reviews.owners(deployment), kind="deployment_paused",
                   message=f"{deployment.snapshot['definition']['name']} v{deployment.artifact_version} was paused: {reason}",
                   process_id=deployment.process_id, deployment_id=deployment.id)
    audit.record(session, actor=None, actor_type="system" if actor == "auto" else "user", actor_name=actor,
                 action="deployment_paused", object_type="deployment", object_id=deployment.id,
                 process_id=deployment.process_id, detail={"reason": reason})
    session.flush()


def pause_if_needed(session: Session, deployment: AgentDeploymentModel) -> str | None:
    if deployment.status != "active":
        return None
    trigger = limits.pause_trigger(session, deployment)
    if trigger:
        pause(session, deployment, reason=f"Paused automatically: {trigger}", actor="auto")
    return trigger


def resume(session: Session, deployment: AgentDeploymentModel, user: UserModel, *, comment: str) -> None:
    if deployment.status != "paused":
        raise DeploymentError(f"only a paused deployment can be resumed; this one is {deployment.status}")
    if not comment.strip():
        raise DeploymentError("say why it's safe to resume")
    deployment.status = "active"
    audit.record(session, actor=user, action="deployment_resumed", object_type="deployment", object_id=deployment.id,
                 process_id=deployment.process_id, detail={"comment": comment, "was_paused_for": deployment.pause_reason})
    deployment.paused_at = None
    deployment.pause_reason = None
    session.flush()


def retire(session: Session, deployment: AgentDeploymentModel, *, actor: str) -> None:
    if deployment.status not in (*LIVE, "pending", "shadow", "external"):
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
    if deployment.status in (*LIVE, "pending", "shadow", "retired"):
        _stop(session, deployment, status="killed", reason="The agent's identity was revoked", actor=user.id)
    session.flush()
