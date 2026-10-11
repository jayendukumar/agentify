"""Epic 29: the application and data catalog -- system-name resolution
(US29.3), CSV import (US29.2), OpenAPI attachment (US29.4), permissions
from data classes (US29.7), and the third-party register (US29.8).

Applications and data assets are context entities (kinds "application"
and "data_asset", app/context/repository.py); this module adds what's
specific to them.
"""

from __future__ import annotations

import csv
import io
import re
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.context import repository as context_repository
from app.db.models import (
    AgentArtifactModel,
    BlueprintOverlayModel,
    ContextEntityModel,
    ProcessElementModel,
    ProcessModel,
    ProcessSystemLinkModel,
)
from app.ids import utcnow
from app.schemas.agents import AgentDefinition, ResourcePermission
from app.schemas.blueprint import AgentSpec
from app.schemas.context import (
    ApiSpecResult,
    ApplicationImportResult,
    ApplicationImportRow,
    ContextEntityCreate,
    ContextEntityUpdate,
    SystemResolution,
    ThirdPartyRegisterRow,
)
from app.schemas.twin import InferredToolSchema
from app.store import NotFoundError

from .endpoints import is_safe
from .openapi import build_tool_schema, load_spec, parse_operations

_GENERIC_WORDS = {"system", "systems", "platform", "application", "app", "tool", "service", "portal", "the"}
_WRITE_CLASSES_NEED_REVIEW = {"Restricted"}


class CatalogError(Exception):
    pass


# ---- name matching --------------------------------------------------------


def normalize(name: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9 ]+", " ", name.lower())).strip()


def _core_tokens(name: str) -> set[str]:
    return {t for t in normalize(name).split() if t not in _GENERIC_WORDS}


def _names(app: ContextEntityModel) -> list[str]:
    return [app.name, *((app.data or {}).get("aliases") or [])]


def match_application(apps: list[ContextEntityModel], system_name: str) -> tuple[ContextEntityModel | None, list[ContextEntityModel]]:
    """(exact unique match, fuzzy candidates). Exact means the normalized
    name or an alias is identical -- the only case that auto-links (Notes:
    anything fuzzy goes to a person)."""
    wanted = normalize(system_name)
    exact = [a for a in apps if any(normalize(n) == wanted for n in _names(a))]
    if len(exact) == 1:
        return exact[0], []
    if len(exact) > 1:
        return None, exact
    tokens = _core_tokens(system_name)
    candidates = []
    for app in apps:
        for name in _names(app):
            other = _core_tokens(name)
            if not tokens or not other:
                continue
            overlap = len(tokens & other) / len(tokens | other)
            if overlap >= 0.5 or tokens <= other or normalize(name) in wanted or wanted in normalize(name):
                candidates.append(app)
                break
    return None, candidates


# ---- per-process resolution (US29.3) --------------------------------------


def _applications(session: Session) -> list[ContextEntityModel]:
    return context_repository.list_entities(session, kind="application", status="confirmed")


def collect_system_names(session: Session, process_id: str) -> dict[str, list[str]]:
    """Every free-text system name used anywhere in the process, with where
    it's used (for the review list)."""
    used: dict[str, list[str]] = {}

    def add(name: str, where: str) -> None:
        name = name.strip()
        if name and where not in used.setdefault(name, []):
            used[name].append(where)

    for element in session.scalars(select(ProcessElementModel).where(ProcessElementModel.process_id == process_id)):
        for name in element.systems_touched or []:
            add(name, f"step '{element.label}'")
    overlay = session.get(BlueprintOverlayModel, process_id)
    if overlay is not None:
        for node in overlay.nodes:
            spec = node.get("agent_spec") or {}
            for name in spec.get("tools_systems_needed") or []:
                add(name, f"blueprint agent '{spec.get('name', node.get('node_id'))}'")
    for artifact in session.scalars(select(AgentArtifactModel).where(AgentArtifactModel.process_id == process_id)):
        for name in (artifact.definition or {}).get("tools_systems_needed") or []:
            add(name, f"agent '{(artifact.definition or {}).get('name', artifact.id)}'")
    return used


def links_for_process(session: Session, process_id: str) -> dict[str, ContextEntityModel]:
    links = session.scalars(select(ProcessSystemLinkModel).where(ProcessSystemLinkModel.process_id == process_id))
    result = {}
    for link in links:
        if link.application_id:
            app = session.get(ContextEntityModel, link.application_id)
            if app is not None and app.status == "confirmed":
                result[link.system_name] = app
    return result


def auto_link(session: Session, process_id: str) -> int:
    """Links every not-yet-linked system name with exactly one exact
    catalog match. Never touches a link a person set."""
    apps = _applications(session)
    if not apps:
        return 0
    existing = {
        link.system_name
        for link in session.scalars(select(ProcessSystemLinkModel).where(ProcessSystemLinkModel.process_id == process_id))
    }
    count = 0
    for name in collect_system_names(session, process_id):
        if name in existing:
            continue
        app, _ = match_application(apps, name)
        if app is not None:
            session.add(ProcessSystemLinkModel(process_id=process_id, system_name=name, application_id=app.id, auto=True))
            count += 1
    session.flush()
    return count


def list_resolutions(session: Session, process_id: str) -> list[SystemResolution]:
    if session.get(ProcessModel, process_id) is None:
        raise NotFoundError("process", process_id)
    auto_link(session, process_id)
    apps = _applications(session)
    links = {
        link.system_name: link
        for link in session.scalars(select(ProcessSystemLinkModel).where(ProcessSystemLinkModel.process_id == process_id))
    }
    result = []
    for name, used_in in sorted(collect_system_names(session, process_id).items()):
        link = links.get(name)
        if link is not None and link.application_id:
            app = session.get(ContextEntityModel, link.application_id)
            result.append(
                SystemResolution(
                    system_name=name,
                    status="linked" if not link.auto else "matched",
                    application_id=link.application_id,
                    application_name=app.name if app else None,
                    auto=link.auto,
                    used_in=used_in,
                )
            )
            continue
        _, candidates = match_application(apps, name)
        status = "unmatched" if link is not None or not candidates else "ambiguous"
        result.append(
            SystemResolution(
                system_name=name,
                status=status,
                candidates=[{"id": c.id, "name": c.name} for c in candidates],
                used_in=used_in,
            )
        )
    return result


def set_link(session: Session, process_id: str, system_name: str, application_id: str | None, *, linked_by: str | None) -> None:
    """A person's choice. `application_id=None` records "not in the
    catalog", which stops it being offered as ambiguous again."""
    if application_id is not None:
        app = context_repository.get_entity_model(session, application_id)
        if app.kind != "application":
            raise CatalogError(f"'{application_id}' is not an application")
    link = session.get(ProcessSystemLinkModel, (process_id, system_name))
    if link is None:
        link = ProcessSystemLinkModel(process_id=process_id, system_name=system_name)
        session.add(link)
    link.application_id = application_id
    link.auto = False
    link.linked_by = linked_by
    link.linked_at = utcnow()
    session.flush()


# ---- CSV import (US29.2) --------------------------------------------------

_COLUMN_ALIASES = {
    "name": ["name", "application", "application name", "app", "system", "system name", "ci name"],
    "aliases": ["aliases", "alias", "also known as", "aka", "other names"],
    "vendor": ["vendor", "supplier", "manufacturer", "provider"],
    "owner": ["owner", "owner role", "business owner", "managed by"],
    "description": ["description", "short description", "purpose"],
    "hosting_region": ["hosting region", "region", "location", "hosting", "data center"],
    "criticality": ["criticality", "business criticality", "tier"],
}


def _column_map(headers: list[str]) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for header in headers:
        key = normalize(header)
        for field, options in _COLUMN_ALIASES.items():
            if key in options and field not in mapping:
                mapping[field] = header
    return mapping


def _criticality(value: str) -> str | None:
    value = normalize(value)
    for level in ("critical", "high", "medium", "low"):
        if level in value:
            return level
    return {"1": "critical", "tier 1": "critical", "2": "high", "tier 2": "high", "3": "medium", "4": "low"}.get(value)


def import_applications_csv(
    session: Session, content: bytes, *, dry_run: bool, created_by: str | None
) -> ApplicationImportResult:
    text = content.decode("utf-8-sig", errors="replace")
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise CatalogError("The CSV has no header row")
    columns = _column_map(reader.fieldnames)
    if "name" not in columns:
        raise CatalogError(f"No name column found (looked for: {', '.join(_COLUMN_ALIASES['name'])})")

    apps = _applications(session)
    seen: dict[str, int] = {}
    result = ApplicationImportResult(dry_run=dry_run)
    for index, row in enumerate(reader, start=2):
        name = (row.get(columns["name"]) or "").strip()
        if not name:
            result.rows.append(ApplicationImportRow(row=index, name="", action="skip", message="no name"))
            continue
        aliases = [a.strip() for a in re.split(r"[;|,]", row.get(columns.get("aliases", ""), "") or "") if a.strip()]
        if normalize(name) in seen:
            result.rows.append(
                ApplicationImportRow(row=index, name=name, action="skip", message=f"duplicate of row {seen[normalize(name)]}")
            )
            continue
        seen[normalize(name)] = index
        data: dict[str, Any] = {"aliases": aliases}
        for field in ("vendor", "owner", "description", "hosting_region"):
            if field in columns and (row.get(columns[field]) or "").strip():
                data[field] = row[columns[field]].strip()
        if "criticality" in columns:
            data["criticality"] = _criticality(row.get(columns["criticality"]) or "")

        matched = None
        for candidate in [name, *aliases]:
            matched, _ = match_application(apps, candidate)
            if matched is not None:
                break
        if matched is None:
            result.rows.append(ApplicationImportRow(row=index, name=name, aliases=aliases, action="create"))
            if not dry_run:
                entity = context_repository.create_entity(
                    session,
                    ContextEntityCreate(kind="application", name=name, data=data),
                    source="import:csv",
                    created_by=created_by,
                )
                apps.append(entity)
                result.created += 1
            continue

        merged = dict(matched.data or {})
        merged_aliases = list(dict.fromkeys([*(merged.get("aliases") or []), *aliases, *([name] if normalize(name) != normalize(matched.name) else [])]))
        merged.update({k: v for k, v in data.items() if k != "aliases" and v})
        merged["aliases"] = merged_aliases
        changed = merged != (matched.data or {})
        result.rows.append(
            ApplicationImportRow(
                row=index,
                name=name,
                aliases=aliases,
                action="update" if changed else "skip",
                matched_application_id=matched.id,
                message=f"matches existing '{matched.name}'" + ("" if changed else " -- nothing new"),
            )
        )
        if changed and not dry_run:
            context_repository.update_entity(
                session, matched.id, ContextEntityUpdate(data=merged, change_note="CSV import"), changed_by=created_by
            )
            result.updated += 1
    return result


# ---- OpenAPI (US29.4) -----------------------------------------------------


def attach_api_spec(session: Session, application_id: str, raw: str | bytes, *, changed_by: str | None) -> ApiSpecResult:
    app = context_repository.get_entity_model(session, application_id)
    if app.kind != "application":
        raise CatalogError(f"'{application_id}' is not an application")
    spec = load_spec(raw)
    operations = parse_operations(spec)
    if not operations:
        raise CatalogError("The OpenAPI document defines no operations")
    info = spec.get("info") or {}
    data = dict(app.data or {})
    data.update(
        {
            "api_spec": spec,
            "api_title": str(info.get("title", "")),
            "api_version": str(info.get("version", "")),
            "api_operations": [op.model_dump() for op in operations],
        }
    )
    context_repository.update_entity(
        session, application_id, ContextEntityUpdate(data=data, change_note="API spec attached"), changed_by=changed_by
    )
    return ApiSpecResult(application_id=application_id, title=data["api_title"], version=data["api_version"], operations=operations)


def safe_endpoint(app: ContextEntityModel) -> dict[str, str] | None:
    for endpoint in (app.data or {}).get("endpoints") or []:
        if is_safe(endpoint.get("url", ""), endpoint.get("environment")):
            return endpoint
    return None


def spec_tool_schemas(session: Session, process_id: str, system_names: list[str]) -> dict[str, InferredToolSchema]:
    """Tool schemas built from linked applications' real specs. Systems
    without a linked, spec'd application are left to LLM inference."""
    links = links_for_process(session, process_id)
    schemas = {}
    from app.schemas.context import ApiOperation

    for name in system_names:
        app = links.get(name)
        operations = (app.data or {}).get("api_operations") if app is not None else None
        if not operations:
            continue
        endpoint = safe_endpoint(app)
        schemas[name] = build_tool_schema(
            name,
            app.id,
            [ApiOperation.model_validate(op) for op in operations],
            base_url=endpoint["url"] if endpoint else None,
            environment=endpoint["environment"] if endpoint else None,
        )
    return schemas


# ---- permissions from data classes (US29.7) -------------------------------


def _classifications(session: Session, app: ContextEntityModel) -> list[str]:
    classes = []
    for asset_id in (app.data or {}).get("data_assets") or []:
        asset = session.get(ContextEntityModel, asset_id)
        if asset is not None and asset.status == "confirmed":
            classes.append((asset.data or {}).get("classification", "Internal"))
    return sorted(set(classes))


def _writes_to(spec: AgentSpec, system_name: str, app: ContextEntityModel) -> bool:
    names = {normalize(n) for n in [system_name, *_names(app)]}
    return any(
        any(n and n in normalize(field.source_or_destination) for n in names) for field in spec.expected_outputs
    )


def apply_catalog_to_definition(
    session: Session, process_id: str, definition: AgentDefinition, spec: AgentSpec
) -> AgentDefinition:
    """US29.3 + US29.7: link each tool contract to its application and set
    permissions from the data the application holds -- read-only unless
    the step explicitly writes to that system, and never default access to
    Restricted data (a person must grant it)."""
    auto_link(session, process_id)
    links = links_for_process(session, process_id)
    permissions = {p.resource: p for p in definition.permissions}
    for contract in definition.tool_contracts:
        app = links.get(contract.system_name)
        if app is None:
            continue
        classes = _classifications(session, app)
        contract.application_id = app.id
        contract.data_classifications = classes
        writes = _writes_to(spec, contract.system_name, app)
        restricted = bool(set(classes) & _WRITE_CLASSES_NEED_REVIEW)
        if writes and not restricted:
            contract.action = "update"
            contract.side_effect = "mutating"
        permission = permissions.get(contract.resource) or ResourcePermission(resource=contract.resource)
        permission.actions = {
            "read": not restricted,
            "update": writes and not restricted,
            "initiate": False,
            "override": False,
        }
        permissions[contract.resource] = permission
    definition.permissions = list(permissions.values())
    return definition


# ---- third-party register (US29.8) ----------------------------------------


def third_party_register(session: Session) -> list[ThirdPartyRegisterRow]:
    rows: dict[str, ThirdPartyRegisterRow] = {}
    for app in _applications(session):
        data = app.data or {}
        rows[app.id] = ThirdPartyRegisterRow(
            application_id=app.id,
            application=app.name,
            vendor=data.get("vendor", ""),
            criticality=data.get("criticality"),
            hosting_region=data.get("hosting_region", ""),
            classifications=_classifications(session, app),
        )
    process_names = {p.id: p.name for p in session.scalars(select(ProcessModel))}
    for artifact in session.scalars(select(AgentArtifactModel)):
        definition = artifact.definition or {}
        links = links_for_process(session, artifact.process_id)
        app_ids = {c.get("application_id") for c in definition.get("tool_contracts", []) if c.get("application_id")}
        app_ids |= {links[name].id for name in definition.get("tools_systems_needed", []) if name in links}
        for app_id in app_ids:
            row = rows.get(app_id)
            if row is None:
                continue
            row.agents.append({"id": artifact.id, "name": definition.get("name", artifact.id), "process_id": artifact.process_id})
            process = {"id": artifact.process_id, "name": process_names.get(artifact.process_id, artifact.process_id)}
            if process not in row.processes:
                row.processes.append(process)
    return sorted(rows.values(), key=lambda r: (-len(r.agents), r.application.lower()))
