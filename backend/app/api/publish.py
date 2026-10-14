from fastapi import APIRouter, HTTPException, Request, status

from app.auth import audit, sod
from app.db import repository
from app.registry.base import RegistryAuthError, RegistryConnectorError, RegistryUnreachableError
from app.schemas.publish import AgentPublication, AgentPublishStatus, PublishRequest

from .deps import AgentPublishDep, CurrentUserDep, DbDep, RegistriesDep

router = APIRouter(prefix="/api/processes/{process_id}/agent-artifacts/{artifact_id}", tags=["publish"])


@router.post("/publish", response_model=AgentPublication)
def publish_agent_artifact(
    process_id: str,
    artifact_id: str,
    body: PublishRequest,
    registries: RegistriesDep,
    db: DbDep,
    request: Request,
    user: AgentPublishDep,
) -> AgentPublication:
    # Epic 37, US37.4: only an approved version, and not by its only approver.
    try:
        sod.check_publisher(db, repository.get_agent_artifact(db, process_id, artifact_id), user)
    except sod.SegregationOfDutiesError as exc:
        audit.record_refusal(
            actor=user, action="publish_agent_artifact", reason=str(exc), object_type="agent", object_id=artifact_id,
            process_id=process_id, request=request,
        )
        raise HTTPException(status.HTTP_403_FORBIDDEN, detail=str(exc)) from exc
    except sod.ApprovalStateError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, detail=str(exc)) from exc
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
    process_id: str, artifact_id: str, publication_id: str, db: DbDep, user: AgentPublishDep
) -> AgentPublication:
    return repository.mark_agent_publication_deployed(db, process_id, artifact_id, publication_id, deployed_by=user.id)
