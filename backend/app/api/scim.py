"""Epic 37, US37.10: the SCIM 2.0 endpoint at /scim/v2 the customer's IdP
(Entra ID, Okta) provisions users and groups through.

Authenticated by a bearer token an admin generates on the Directory page
(stored hashed, several may be live for rotation), not by a session: the
IdP calls server to server, so there's no cookie and no CSRF token.
Every answer, errors included, is `application/scim+json`.
"""

import hashlib
from datetime import timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, Header, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import select

from app.tenancy import identity
from app.db.models import ScimTokenModel
from app.ids import utcnow
from app.scim import service
from app.scim.protocol import (
    ENTERPRISE_USER,
    GROUP_SCHEMA,
    MEDIA_TYPE,
    USER_SCHEMA,
    ScimError,
    list_response,
    parse_filter,
)

from .deps import DbDep

router = APIRouter(prefix="/scim/v2", tags=["scim"])

_USER_FILTERS = {"username", "externalid", "id", "emails", "emails.value"}
_GROUP_FILTERS = {"displayname", "externalid", "id"}


def scim_response(content: Any, status: int = 200, headers: dict[str, str] | None = None) -> JSONResponse:
    return JSONResponse(content, status_code=status, media_type=MEDIA_TYPE, headers=headers)


async def scim_error_handler(request: Request, exc: ScimError) -> JSONResponse:
    headers = {"WWW-Authenticate": 'Bearer realm="scim"'} if exc.status == 401 else None
    return scim_response(exc.body(), exc.status, headers)


def scim_client(db: DbDep, authorization: Annotated[str | None, Header()] = None) -> service.Client:
    scheme, _, token = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise ScimError(401, "A bearer token is required")
    row = db.scalar(select(ScimTokenModel).where(ScimTokenModel.token_hash == hashlib.sha256(token.encode()).hexdigest()))
    if row is None or row.revoked_at is not None:
        raise ScimError(401, "Invalid or revoked token")
    now = utcnow().replace(tzinfo=None)
    if row.last_used_at is None or now - row.last_used_at > timedelta(minutes=1):
        row.last_used_at = now
    return service.Client(row)


ClientDep = Annotated[service.Client, Depends(scim_client)]
BodyDep = Annotated[dict[str, Any], Body(media_type=MEDIA_TYPE)]


def _page(start_index: int, count: int | None) -> tuple[int, int]:
    return max(start_index, 1), min(max(count if count is not None else 100, 0), service.MAX_PAGE)


def _excludes_members(excluded: str | None) -> bool:
    return "members" in {a.strip().lower() for a in (excluded or "").split(",")}


# -- discovery (RFC 7644 section 4) ------------------------------------------------------


@router.get("/ServiceProviderConfig")
def service_provider_config(client: ClientDep, db: DbDep) -> JSONResponse:
    del client
    return scim_response({
        "schemas": ["urn:ietf:params:scim:schemas:core:2.0:ServiceProviderConfig"],
        "documentationUri": f"{identity.resolve(db).api_url}/docs",
        "patch": {"supported": True},
        "bulk": {"supported": False, "maxOperations": 0, "maxPayloadSize": 0},
        "filter": {"supported": True, "maxResults": service.MAX_PAGE},
        "changePassword": {"supported": False},
        "sort": {"supported": False},
        "etag": {"supported": False},
        "authenticationSchemes": [{
            "type": "oauthbearertoken", "name": "Bearer token", "primary": True,
            "description": "A token generated on Axyntro's Directory page",
        }],
        "meta": {"resourceType": "ServiceProviderConfig"},
    })


def _attribute(name: str, type_: str = "string", **extra: Any) -> dict[str, Any]:
    return {"name": name, "type": type_, "multiValued": False, "required": False, "caseExact": False,
            "mutability": "readWrite", "returned": "default", "uniqueness": "none", **extra}


_SCHEMAS = [
    {
        "id": USER_SCHEMA, "name": "User", "description": "A person who may sign in",
        "attributes": [
            _attribute("userName", required=True, uniqueness="server"),
            _attribute("name", "complex", subAttributes=[_attribute(n) for n in ("formatted", "familyName", "givenName")]),
            _attribute("displayName"),
            _attribute("active", "boolean"),
            _attribute("emails", "complex", multiValued=True,
                       subAttributes=[_attribute("value"), _attribute("type"), _attribute("primary", "boolean")]),
            _attribute("groups", "complex", multiValued=True, mutability="readOnly",
                       subAttributes=[_attribute("value", mutability="readOnly"), _attribute("display", mutability="readOnly")]),
        ],
    },
    {
        "id": GROUP_SCHEMA, "name": "Group", "description": "A directory group; group mappings turn it into roles",
        "attributes": [
            _attribute("displayName", required=True, uniqueness="server"),
            _attribute("members", "complex", multiValued=True,
                       subAttributes=[_attribute("value", mutability="immutable"), _attribute("display", mutability="readOnly")]),
        ],
    },
    {
        "id": ENTERPRISE_USER, "name": "EnterpriseUser", "description": "Stored as sent; not used by Axyntro",
        "attributes": [_attribute(n) for n in ("employeeNumber", "department", "organization", "division")],
    },
]
for _schema in _SCHEMAS:
    _schema["schemas"] = ["urn:ietf:params:scim:schemas:core:2.0:Schema"]
    _schema["meta"] = {"resourceType": "Schema"}

_RESOURCE_TYPES = [
    {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "User", "name": "User",
     "endpoint": "/Users", "schema": USER_SCHEMA,
     "schemaExtensions": [{"schema": ENTERPRISE_USER, "required": False}], "meta": {"resourceType": "ResourceType"}},
    {"schemas": ["urn:ietf:params:scim:schemas:core:2.0:ResourceType"], "id": "Group", "name": "Group",
     "endpoint": "/Groups", "schema": GROUP_SCHEMA, "meta": {"resourceType": "ResourceType"}},
]


@router.get("/Schemas")
def schemas(client: ClientDep) -> JSONResponse:
    del client
    return scim_response(list_response(_SCHEMAS, len(_SCHEMAS), 1))


@router.get("/Schemas/{schema_id}")
def schema(schema_id: str, client: ClientDep) -> JSONResponse:
    del client
    found = next((s for s in _SCHEMAS if s["id"] == schema_id), None)
    if found is None:
        raise ScimError(404, f"Schema {schema_id} not found")
    return scim_response(found)


@router.get("/ResourceTypes")
def resource_types(client: ClientDep) -> JSONResponse:
    del client
    return scim_response(list_response(_RESOURCE_TYPES, len(_RESOURCE_TYPES), 1))


@router.get("/ResourceTypes/{type_id}")
def resource_type(type_id: str, client: ClientDep) -> JSONResponse:
    del client
    found = next((t for t in _RESOURCE_TYPES if t["id"] == type_id), None)
    if found is None:
        raise ScimError(404, f"Resource type {type_id} not found")
    return scim_response(found)


# -- Users ------------------------------------------------------------------------------


@router.get("/Users")
def list_users(
    db: DbDep, client: ClientDep, filter: str | None = None, startIndex: int = 1, count: int | None = None,  # noqa: A002, N803
) -> JSONResponse:
    del client
    start, size = _page(startIndex, count)
    resources, total = service.list_users(db, parse_filter(filter, _USER_FILTERS), start, size)
    return scim_response(list_response(resources, total, start))


@router.get("/Users/{user_id}")
def get_user(user_id: str, db: DbDep, client: ClientDep) -> JSONResponse:
    del client
    return scim_response(service.get_user(db, user_id))


@router.post("/Users")
def create_user(body: BodyDep, db: DbDep, client: ClientDep) -> JSONResponse:
    resource = service.create_user(db, client, body)
    return scim_response(resource, 201, {"Location": resource["meta"]["location"]})


@router.put("/Users/{user_id}")
def replace_user(user_id: str, body: BodyDep, db: DbDep, client: ClientDep) -> JSONResponse:
    return scim_response(service.replace_user(db, client, user_id, body))


@router.patch("/Users/{user_id}")
def patch_user(user_id: str, body: BodyDep, db: DbDep, client: ClientDep) -> JSONResponse:
    return scim_response(service.patch_user(db, client, user_id, body))


@router.delete("/Users/{user_id}", status_code=204)
def delete_user(user_id: str, db: DbDep, client: ClientDep) -> Response:
    service.delete_user(db, client, user_id)
    return Response(status_code=204)


# -- Groups -----------------------------------------------------------------------------


@router.get("/Groups")
def list_groups(
    db: DbDep, client: ClientDep, filter: str | None = None, startIndex: int = 1, count: int | None = None,  # noqa: A002, N803
    excludedAttributes: str | None = None,  # noqa: N803
) -> JSONResponse:
    del client
    start, size = _page(startIndex, count)
    resources, total = service.list_groups(
        db, parse_filter(filter, _GROUP_FILTERS), start, size, members=not _excludes_members(excludedAttributes)
    )
    return scim_response(list_response(resources, total, start))


@router.get("/Groups/{group_id}")
def get_group(group_id: str, db: DbDep, client: ClientDep, excludedAttributes: str | None = None) -> JSONResponse:  # noqa: N803
    del client
    return scim_response(service.get_group(db, group_id, members=not _excludes_members(excludedAttributes)))


@router.post("/Groups")
def create_group(body: BodyDep, db: DbDep, client: ClientDep) -> JSONResponse:
    resource = service.create_group(db, client, body)
    return scim_response(resource, 201, {"Location": resource["meta"]["location"]})


@router.put("/Groups/{group_id}")
def replace_group(group_id: str, body: BodyDep, db: DbDep, client: ClientDep) -> JSONResponse:
    return scim_response(service.replace_group(db, client, group_id, body))


@router.patch("/Groups/{group_id}", status_code=204)
def patch_group(group_id: str, body: BodyDep, db: DbDep, client: ClientDep) -> Response:
    """204, as Entra ID and Okta expect for group changes (RFC 7644 allows
    200 with the resource or 204)."""
    service.patch_group(db, client, group_id, body)
    return Response(status_code=204)


@router.delete("/Groups/{group_id}", status_code=204)
def delete_group(group_id: str, db: DbDep, client: ClientDep) -> Response:
    service.delete_group(db, client, group_id)
    return Response(status_code=204)
