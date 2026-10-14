from fastapi import APIRouter, HTTPException, Query, Request, Response, status

from app.agents.generation import AgentGenerationError
from app.auth import audit, sod
from app.auth.views import agent_approval_status
from app.context.compliance import ComplianceEvidence, compliance_evidence, evidence_markdown, unevidenced_controls
from app.db import repository
from app.schemas.agents import AgentArtifact, AgentGovernanceUpdate
from app.schemas.governance import ApprovalDecision, ApprovalStatus

from .deps import AgentApproveDep, AgentBuildDep, AgentReleaseCandidateDep, CurrentUserDep, DbDep

router = APIRouter(prefix="/api/processes/{process_id}/blueprint", tags=["agents"])


@router.post("/nodes/{node_id}/agent-artifact", response_model=AgentArtifact)
def generate_agent_artifact(process_id: str, node_id: str, db: DbDep, user: AgentBuildDep) -> AgentArtifact:
    repository.get_process(db, process_id)  # 404s if missing
    try:
        return repository.generate_agent_artifact(db, process_id, node_id, generated_by=user.id)
    except AgentGenerationError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.get("/agent-artifacts", response_model=list[AgentArtifact])
def list_agent_artifacts(process_id: str, db: DbDep, user: CurrentUserDep) -> list[AgentArtifact]:
    del user
    return repository.list_agent_artifacts(db, process_id)


@router.patch("/agent-artifacts/{artifact_id}/governance", response_model=AgentArtifact)
def update_agent_governance(
    process_id: str, artifact_id: str, update: AgentGovernanceUpdate, db: DbDep, user: AgentBuildDep
) -> AgentArtifact:
    """Epic 20, US20.1: editor-only, same tier as generate/publish (Epic
    15's US15.6 precedent) -- widening an agent's declared permissions is
    at least as consequential as publishing it."""
    repository.get_process(db, process_id)  # 404s if missing
    return repository.update_agent_governance(db, process_id, artifact_id, update, changed_by=user.id)


@router.post("/agent-artifacts/{artifact_id}/mark-release-candidate", response_model=AgentArtifact)
def mark_agent_release_candidate(process_id: str, artifact_id: str, db: DbDep, user: AgentReleaseCandidateDep) -> AgentArtifact:
    """Epic 23, US23.4: editor-only, same tier as governance/publish.

    Epic 30, US30.6: refused while any applicable, testable control lacks a
    passing situation on the current agent version and pinned context."""
    del user
    repository.get_process(db, process_id)  # 404s if missing
    artifact = repository.get_agent_artifact(db, process_id, artifact_id)
    missing = unevidenced_controls(db, artifact)
    if missing:
        names = "; ".join(f"{c.code or c.name} ({c.status.replace('_', ' ')})" for c in missing)
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            detail=f"Every rule this agent follows needs a passing test on its current version first -- {names}",
        )
    # Epic 35, US35.7: the statistical gate, when this agent has criteria.
    if artifact.release_criteria:
        from app.batches.results import ReleaseCriteria, gate

        result = gate(db, artifact, ReleaseCriteria.model_validate(artifact.release_criteria))
        if not result.passed:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Release criteria not met -- " + "; ".join(result.reasons))
    return repository.mark_agent_release_candidate(db, process_id, artifact_id)


@router.get("/agent-artifacts/{artifact_id}/compliance-evidence", response_model=ComplianceEvidence)
def get_compliance_evidence(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> ComplianceEvidence:
    """Epic 30, US30.7."""
    del user
    return compliance_evidence(db, repository.get_agent_artifact(db, process_id, artifact_id))


@router.get("/agent-artifacts/{artifact_id}/compliance-evidence/export")
def export_compliance_evidence(
    process_id: str,
    artifact_id: str,
    db: DbDep,
    user: CurrentUserDep,
    format: str = Query("markdown", pattern="^(markdown)$"),
) -> Response:
    del format
    process = repository.get_process(db, process_id)
    evidence = compliance_evidence(db, repository.get_agent_artifact(db, process_id, artifact_id))
    # Epic 31, US31.10: an evidence export carries rule and policy text out.
    from app.context.audit import record

    record(
        db, actor_type="user", actor=user.id, action="export_evidence", process_id=process_id,
        entity_ids=[c.control_id for c in evidence.controls], detail=f"agent {artifact_id}",
    )
    return Response(
        content=evidence_markdown(evidence, process_name=process.name),
        media_type="text/markdown",
        headers={"Content-Disposition": f'attachment; filename="compliance-evidence-{artifact_id}.md"'},
    )


@router.get("/agent-artifacts/{artifact_id}/approval", response_model=ApprovalStatus)
def get_agent_approval(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> ApprovalStatus:
    """Epic 37, US37.3/US37.4: who changed this version, who approved it,
    and whether the signed-in user may approve it (and if not, why)."""
    return agent_approval_status(db, repository.get_agent_artifact(db, process_id, artifact_id), user)


@router.post("/agent-artifacts/{artifact_id}/approve", response_model=AgentArtifact)
def approve_agent_artifact(
    process_id: str,
    artifact_id: str,
    db: DbDep,
    request: Request,
    user: AgentApproveDep,
    body: ApprovalDecision | None = None,
) -> AgentArtifact:
    """Epic 37, US37.4/US37.5: refused for anyone who made a high-risk
    change to this agent since its last approval; APPROVED once the
    required number of independent approvers have approved."""
    repository.get_process(db, process_id)  # 404s if missing
    try:
        return repository.approve_agent_artifact(
            db, process_id, artifact_id, approver=user, comment=body.comment if body else None
        )
    except sod.SegregationOfDutiesError as exc:
        audit.record_refusal(
            actor=user, action="approve_agent_artifact", reason=str(exc), object_type="agent", object_id=artifact_id,
            process_id=process_id, request=request,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except sod.ApprovalStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
