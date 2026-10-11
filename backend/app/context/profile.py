"""Epic 27, US27.3: the organization profile wizard. Turns a short
questionnaire into business units, jurisdictions, a starter application
list, roles, and an approval-authority matrix -- idempotently, so the
wizard can be re-run to add to or correct its answers without
duplicating anything.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.schemas.context import (
    ContextEntityCreate,
    ContextEntityUpdate,
    OrganizationProfileAnswers,
    OrganizationUpdate,
    ProfileResult,
)

from . import packs, repository

SOURCE = "profile_wizard"


def _ensure(session: Session, kind: str, name: str, created: dict[str, int], *, created_by: str | None, data: dict | None = None):
    existing = repository.find_entity(session, kind, name=name)
    if existing is not None:
        return existing
    created[kind] = created.get(kind, 0) + 1
    return repository.create_entity(
        session,
        ContextEntityCreate(kind=kind, name=name, data=data or {}),  # type: ignore[arg-type]
        source=SOURCE,
        created_by=created_by,
    )


def apply_profile(session: Session, answers: OrganizationProfileAnswers, *, created_by: str | None = None) -> ProfileResult:
    repository.update_organization(
        session, OrganizationUpdate(name=answers.name, industry=answers.industry, size=answers.size)
    )
    created: dict[str, int] = {}

    for code in answers.countries:
        if code.strip() and repository.find_entity(session, "jurisdiction", code=code) is None:
            packs.ensure_jurisdiction(session, code, created_by=created_by, source=SOURCE)
            created["jurisdiction"] = created.get("jurisdiction", 0) + 1

    # A small business is one unit; name it after the organization rather
    # than leaving business-unit scope impossible to select.
    unit_names = [n.strip() for n in answers.business_units if n.strip()] or [answers.name]
    for name in unit_names:
        _ensure(session, "business_unit", name, created, created_by=created_by)

    for name in answers.applications:
        if name.strip():
            _ensure(session, "application", name.strip(), created, created_by=created_by)

    for rule in answers.approval_rules:
        if not rule.role.strip() or not rule.decision_type.strip():
            continue
        role = _ensure(session, "role", rule.role.strip(), created, created_by=created_by)
        authority_data = {
            "role_id": role.id,
            "role_name": role.name,
            "decision_type": rule.decision_type.strip(),
            "limit_amount": rule.limit_amount,
            "currency": rule.currency,
        }
        existing = next(
            (
                a
                for a in repository.list_entities(session, kind="approval_authority", parent_id=role.id)
                if (a.data or {}).get("decision_type", "").lower() == rule.decision_type.strip().lower()
            ),
            None,
        )
        if existing is None:
            repository.create_entity(
                session,
                ContextEntityCreate(
                    kind="approval_authority",
                    name=f"{role.name}: {rule.decision_type.strip()}",
                    data=authority_data,
                    parent_id=role.id,
                ),
                source=SOURCE,
                created_by=created_by,
            )
            created["approval_authority"] = created.get("approval_authority", 0) + 1
        else:
            repository.update_entity(
                session, existing.id, ContextEntityUpdate(data=authority_data, change_note="profile wizard"), changed_by=created_by
            )

    repository.set_organization_profile_value(session, "answers", answers.model_dump())

    applied = []
    for pack_id in answers.apply_packs:
        packs.apply_pack(session, pack_id, created_by=created_by)
        applied.append(pack_id)

    return ProfileResult(
        organization=repository.get_organization(session),
        created=created,
        applied_packs=applied,
        suggested_packs=packs.suggested_pack_ids(answers.countries, answers.industry),
    )
