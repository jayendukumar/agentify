from fastapi import APIRouter, HTTPException, status

from app.db import repository
from app.registry.base import RegistryAuthError, RegistryConnectorError, RegistryUnreachableError
from app.schemas.publish import AgentPublication, AgentPublishStatus, PublishRequest

from .deps import CurrentUserDep, DbDep, EditorDep, RegistriesDep

router = APIRouter(prefix="/api/processes/{process_id}/agent-artifacts/{artifact_id}", tags=["publish"])


@router.post("/publish", response_model=AgentPublication)
def publish_agent_artifact(
    process_id: str, artifact_id: str, body: PublishRequest, registries: RegistriesDep, db: DbDep, user: EditorDep
) -> AgentPublication:
    connector = registries.get(body.registry_name)
    if connector is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail=f"Unknown registry '{body.registry_name}'")

    try:
        return repository.publish_agent_artifact(connector, db, process_id, artifact_id, published_by=user.id)
    except RegistryAuthError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Registry '{body.registry_name}' rejected credentials: {exc}"
        ) from exc
    except RegistryUnreachableError as exc:
        raise HTTPException(
            status.HTTP_502_BAD_GATEWAY, detail=f"Registry '{body.registry_name}' is unreachable: {exc}"
        ) from exc
    except RegistryConnectorError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail=str(exc)) from exc


@router.get("/publish-status", response_model=AgentPublishStatus)
def get_publish_status(process_id: str, artifact_id: str, db: DbDep, user: CurrentUserDep) -> AgentPublishStatus:
    del user
    return repository.get_agent_publish_status(db, process_id, artifact_id)


@router.post("/publications/{publication_id}/mark-deployed", response_model=AgentPublication)
def mark_publication_deployed(
    process_id: str, artifact_id: str, publication_id: str, db: DbDep, user: EditorDep
) -> AgentPublication:
    return repository.mark_agent_publication_deployed(db, process_id, artifact_id, publication_id, deployed_by=user.id)
