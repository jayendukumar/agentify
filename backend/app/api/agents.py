from fastapi import APIRouter, HTTPException, status

from app.agents.generation import AgentGenerationError
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
