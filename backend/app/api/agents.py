from fastapi import APIRouter, HTTPException, Query, Response, status

from app.agents.generation import AgentGenerationError
from app.context.compliance import ComplianceEvidence, compliance_evidence, evidence_markdown, unevidenced_controls
from app.db import repository
from app.schemas.agents import AgentArtifact, AgentGovernanceUpdate

from .deps import CurrentUserDep, DbDep, EditorDep

router = APIRouter(prefix="/api/processes/{process_id}/blueprint", tags=["agents"])


@router.post("/nodes/{node_id}/agent-artifact", response_model=AgentArtifact)
def generate_agent_artifact(process_id: str, node_id: str, db: DbDep, user: EditorDep) -> AgentArtifact:
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
    process_id: str, artifact_id: str, update: AgentGovernanceUpdate, db: DbDep, user: EditorDep
) -> AgentArtifact:
    """Epic 20, US20.1: editor-only, same tier as generate/publish (Epic
    15's US15.6 precedent) -- widening an agent's declared permissions is
    at least as consequential as publishing it."""
    del user
    repository.get_process(db, process_id)  # 404s if missing
    return repository.update_agent_governance(db, process_id, artifact_id, update)


@router.post("/agent-artifacts/{artifact_id}/mark-release-candidate", response_model=AgentArtifact)
def mark_agent_release_candidate(process_id: str, artifact_id: str, db: DbDep, user: EditorDep) -> AgentArtifact:
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


@router.post("/agent-artifacts/{artifact_id}/approve", response_model=AgentArtifact)
def approve_agent_artifact(process_id: str, artifact_id: str, db: DbDep, user: EditorDep) -> AgentArtifact:
    repository.get_process(db, process_id)  # 404s if missing
    return repository.approve_agent_artifact(db, process_id, artifact_id, approved_by=user.id)
