"""Epic 27, US27.7: curated starter regulatory packs.

Packs are versioned JSON files shipped with the product
(app/context/starter_packs/). Applying a pack upserts its regulations,
obligations, controls, and glossary terms by `code`: a first apply
creates them as confirmed (the pack is curated content, not an
extraction to review), and re-applying a newer pack version updates the
same entities in place -- bumping their versions -- so anything pinned
to the old version can tell. The applied version per pack is recorded on
the organization's profile.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.schemas.context import (
    AppliesTo,
    ContextEntityCreate,
    ContextEntityUpdate,
    StarterPackApplyResult,
    StarterPackSummary,
)
from app.store import NotFoundError

from . import repository
from .jurisdictions import builtin_name, builtin_parent, normalize_code

PACK_DIR = Path(__file__).parent / "starter_packs"


class _PackControl(BaseModel):
    code: str
    title: str
    description: str = ""
    control_type: str = "other"
    requires_human_review: bool = False
    required_guardrails: list[str] = []
    review_role: str | None = None


class _PackObligation(BaseModel):
    code: str
    title: str
    text: str = ""
    data_classes: list[str] = []
    controls: list[_PackControl] = []


class _PackRegulation(BaseModel):
    code: str
    name: str
    summary: str = ""
    url: str | None = None
    obligations: list[_PackObligation] = []


class _PackGlossaryTerm(BaseModel):
    term: str
    definition: str
    synonyms: list[str] = []


class StarterPack(BaseModel):
    id: str
    version: str
    title: str
    description: str
    disclaimer: str
    industries: list[str] = []
    jurisdictions: list[str] = []
    changelog: list[str] = []
    regulations: list[_PackRegulation] = []
    glossary: list[_PackGlossaryTerm] = []


@lru_cache
def load_packs() -> dict[str, StarterPack]:
    packs = {}
    for path in sorted(PACK_DIR.glob("*.json")):
        pack = StarterPack.model_validate(json.loads(path.read_text(encoding="utf-8")))
        packs[pack.id] = pack
    return packs


def get_pack(pack_id: str) -> StarterPack:
    pack = load_packs().get(pack_id)
    if pack is None:
        raise NotFoundError("starter pack", pack_id)
    return pack


def _jurisdiction_matches(pack: StarterPack, countries: list[str]) -> bool:
    expanded: set[str] = set()
    for code in countries:
        code = normalize_code(code)
        while code and code not in expanded:
            expanded.add(code)
            code = builtin_parent(code) or ""
    return bool(expanded & {normalize_code(c) for c in pack.jurisdictions})


def suggested_pack_ids(countries: list[str], industry: str | None) -> list[str]:
    """A pack is suggested when one of its jurisdictions covers one of the
    organization's countries and, if the pack names industries, the
    organization's industry mentions one of them."""
    industry_text = (industry or "").lower()
    suggested = []
    for pack in load_packs().values():
        if not _jurisdiction_matches(pack, countries):
            continue
        if pack.industries and not any(term in industry_text for term in pack.industries):
            continue
        suggested.append(pack.id)
    return suggested


def list_pack_summaries(session: Session) -> list[StarterPackSummary]:
    organization = repository.get_organization(session)
    countries = [e.code for e in repository.list_entities(session, kind="jurisdiction") if e.code]
    suggested = set(suggested_pack_ids(countries, organization.industry))
    return [
        StarterPackSummary(
            id=pack.id,
            version=pack.version,
            title=pack.title,
            description=pack.description,
            disclaimer=pack.disclaimer,
            industries=pack.industries,
            jurisdictions=pack.jurisdictions,
            obligation_count=sum(len(r.obligations) for r in pack.regulations),
            control_count=sum(len(o.controls) for r in pack.regulations for o in r.obligations),
            applied_version=organization.applied_packs.get(pack.id),
            suggested=pack.id in suggested,
        )
        for pack in load_packs().values()
    ]


def ensure_jurisdiction(session: Session, code: str, *, created_by: str | None = None, source: str = "manual") -> str:
    code = normalize_code(code)
    existing = repository.find_entity(session, "jurisdiction", code=code)
    if existing is None:
        parent = builtin_parent(code)
        if parent:
            ensure_jurisdiction(session, parent, created_by=created_by, source=source)
        repository.create_entity(
            session,
            ContextEntityCreate(kind="jurisdiction", code=code, name=builtin_name(code), data={"parent_code": parent}),
            source=source,
            created_by=created_by,
        )
    return code


def _upsert(
    session: Session,
    result: StarterPackApplyResult,
    *,
    kind: str,
    code: str,
    name: str,
    data: dict[str, Any],
    applies_to: AppliesTo,
    parent_id: str | None,
    source: str,
    created_by: str | None,
) -> str:
    existing = repository.find_entity(session, kind, code=code)
    if existing is None:
        entity = repository.create_entity(
            session,
            ContextEntityCreate(kind=kind, code=code, name=name, data=data, applies_to=applies_to, parent_id=parent_id),  # type: ignore[arg-type]
            source=source,
            created_by=created_by,
        )
        result.created += 1
        return entity.id
    before = existing.version
    repository.update_entity(
        session,
        existing.id,
        ContextEntityUpdate(name=name, data=data, applies_to=applies_to, parent_id=parent_id, change_note=f"updated by {source}"),
        changed_by=created_by,
    )
    if existing.version != before:
        existing.source = source
        result.updated += 1
    else:
        result.unchanged += 1
    return existing.id


def apply_pack(session: Session, pack_id: str, *, created_by: str | None = None) -> StarterPackApplyResult:
    pack = get_pack(pack_id)
    source = f"starter_pack:{pack.id}@{pack.version}"
    result = StarterPackApplyResult(pack_id=pack.id, version=pack.version)
    jurisdictions = [ensure_jurisdiction(session, code, created_by=created_by, source=source) for code in pack.jurisdictions]

    for regulation in pack.regulations:
        regulation_id = _upsert(
            session,
            result,
            kind="regulation",
            code=regulation.code,
            name=regulation.name,
            data={"summary": regulation.summary, "url": regulation.url},
            applies_to=AppliesTo(jurisdictions=jurisdictions),
            parent_id=None,
            source=source,
            created_by=created_by,
        )
        for obligation in regulation.obligations:
            scope = AppliesTo(jurisdictions=jurisdictions, data_classes=obligation.data_classes)
            obligation_id = _upsert(
                session,
                result,
                kind="obligation",
                code=obligation.code,
                name=obligation.title,
                data={"text": obligation.text},
                applies_to=scope,
                parent_id=regulation_id,
                source=source,
                created_by=created_by,
            )
            for control in obligation.controls:
                _upsert(
                    session,
                    result,
                    kind="control",
                    code=control.code,
                    name=control.title,
                    data=control.model_dump(exclude={"code", "title"}),
                    applies_to=scope,
                    parent_id=obligation_id,
                    source=source,
                    created_by=created_by,
                )

    for term in pack.glossary:
        _upsert(
            session,
            result,
            kind="glossary_term",
            code=term.term.lower(),
            name=term.term,
            data={"definition": term.definition, "synonyms": term.synonyms},
            applies_to=AppliesTo(jurisdictions=jurisdictions),
            parent_id=None,
            source=source,
            created_by=created_by,
        )

    applied = dict(repository.get_organization(session).applied_packs)
    applied[pack.id] = pack.version
    repository.set_organization_profile_value(session, "applied_packs", applied)
    return result
