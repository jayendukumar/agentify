"""Epic 29: the application and data catalog API -- CSV import, API specs,
endpoints, per-process system-name resolution, and the third-party
register. Applications and data assets themselves are ordinary context
entities (kinds "application"/"data_asset") created and edited through
app/api/organization.py's entity endpoints."""

from __future__ import annotations

from fastapi import APIRouter, File, HTTPException, Query, UploadFile, status

from app.catalog import service
from app.catalog.endpoints import UnsafeEndpointError, check_endpoint
from app.catalog.openapi import OpenApiError
from app.context import repository as context_repository
from app.schemas.context import (
    ApiSpecResult,
    ApplicationEndpoint,
    ApplicationImportResult,
    ContextEntity,
    ContextEntityUpdate,
    SystemLinkRequest,
    SystemResolution,
    ThirdPartyRegisterRow,
)

from .deps import AgentBuildDep, CatalogEditDep, CurrentUserDep, DbDep, ProcessScope

router = APIRouter(prefix="/api/organization/applications", tags=["catalog"])

_MAX_SIZE_BYTES = 10 * 1024 * 1024


@router.post("/import", response_model=ApplicationImportResult)
async def import_applications(
    db: DbDep, user: CatalogEditDep, file: UploadFile = File(...), dry_run: bool = Query(default=True)
) -> ApplicationImportResult:
    """US29.2: preview first (`dry_run=true`, the default), then import."""
    content = await file.read()
    if len(content) > _MAX_SIZE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File exceeds the 10MB limit")
    try:
        return service.import_applications_csv(db, content, dry_run=dry_run, created_by=user.id)
    except service.CatalogError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{application_id}/api-spec", response_model=ApiSpecResult)
async def attach_api_spec(application_id: str, db: DbDep, user: CatalogEditDep, file: UploadFile = File(...)) -> ApiSpecResult:
    """US29.4: attach an OpenAPI document (JSON or YAML)."""
    content = await file.read()
    if len(content) > _MAX_SIZE_BYTES:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="File exceeds the 10MB limit")
    try:
        return service.attach_api_spec(db, application_id, content, changed_by=user.id)
    except (service.CatalogError, OpenApiError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc


@router.put("/{application_id}/endpoints", response_model=ContextEntity)
def set_endpoints(
    application_id: str, body: list[ApplicationEndpoint], db: DbDep, user: CatalogEditDep
) -> ContextEntity:
    """US29.5: register endpoints. A production-tagged endpoint may be
    recorded for reference but is never called. Any other endpoint must
    pass the production-safety check, or the whole request is refused."""
    app = context_repository.get_entity_model(db, application_id)
    if app.kind != "application":
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=f"'{application_id}' is not an application")
    for endpoint in body:
        if endpoint.environment == "production":
            continue
        try:
            check_endpoint(endpoint.url, endpoint.environment)
        except UnsafeEndpointError as exc:
            raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    data = dict(app.data or {})
    data["endpoints"] = [e.model_dump() for e in body]
    entity = context_repository.update_entity(
        db, application_id, ContextEntityUpdate(data=data, change_note="endpoints updated"), changed_by=user.id
    )
    return context_repository.to_entity(entity)


@router.get("/register", response_model=list[ThirdPartyRegisterRow])
def third_party_register(db: DbDep, user: CurrentUserDep) -> list[ThirdPartyRegisterRow]:
    """US29.8."""
    del user
    return service.third_party_register(db)


process_router = APIRouter(prefix="/api/processes/{process_id}/systems", tags=["catalog"], dependencies=[ProcessScope])


@process_router.get("", response_model=list[SystemResolution])
def list_system_resolutions(process_id: str, db: DbDep, user: CurrentUserDep) -> list[SystemResolution]:
    """US29.3: every system name in the process and what it resolves to.
    Exact matches are linked automatically on read."""
    del user
    return service.list_resolutions(db, process_id)


@process_router.put("/{system_name}", response_model=list[SystemResolution])
def link_system(
    process_id: str, system_name: str, body: SystemLinkRequest, db: DbDep, user: AgentBuildDep
) -> list[SystemResolution]:
    try:
        service.set_link(db, process_id, system_name, body.application_id, linked_by=user.id)
    except service.CatalogError as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return service.list_resolutions(db, process_id)
