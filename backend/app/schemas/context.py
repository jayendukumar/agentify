"""Epic 27: the organization context model -- see planning/epics/27-
organization-context-foundation.md and planning/enterprise-context-
strategy.md.

Every entity kind shares one envelope (`ContextEntity`): scope
(`applies_to`), confirmation status, citations, source, and version. The
kind-specific fields live in `data` and are validated against the
matching `*Data` model below on every write, so the generic table
(app/db/models.py's ContextEntityModel) never holds a malformed entity.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

ContextKind = Literal[
    "business_unit",
    "jurisdiction",
    "role",
    "approval_authority",
    "regulation",
    "obligation",
    "control",
    "policy",
    "glossary_term",
    # Epic 29
    "application",
    "data_class",
    "data_asset",
]

ContextStatus = Literal["proposed", "confirmed", "dismissed", "retired"]

ControlType = Literal["human_review", "logging", "segregation_of_duties", "data_protection", "notification", "other"]


class AppliesTo(BaseModel):
    """US27.5: where an entity applies. An empty list on a dimension means
    "no restriction on that dimension", so `AppliesTo()` is
    organization-wide. Jurisdiction codes match hierarchically: an entity
    scoped to "EU" applies to a process in "DE" (see
    app/context/applicability.py)."""

    business_units: list[str] = []
    jurisdictions: list[str] = []
    data_classes: list[str] = []
    process_types: list[str] = []
    systems: list[str] = []


class Citation(BaseModel):
    """US28.3's citation contract, introduced here because extraction
    (US27.6) already needs it: which passage of which policy an entity
    came from."""

    passage_id: str | None = None
    policy_id: str | None = None
    clause_ref: str | None = None
    quote: str = ""


# ---- kind-specific data ---------------------------------------------------


class LlmPolicy(BaseModel):
    """Epic 31, US31.8: which model providers a business unit's context may
    be sent to, where they must run, an optional self-hosted
    OpenAI-compatible endpoint, and whether to redact personal data first.
    Enforced by app/llm/routing.py."""

    allowed_providers: list[str] = []
    residency: list[str] = []
    base_url: str | None = None
    api_key_env: str | None = None
    provider_name: str | None = None
    provider_region: str | None = None
    model: str | None = None
    redact_pii: bool = False


class BusinessUnitData(BaseModel):
    description: str = ""
    llm_policy: LlmPolicy | None = None


class JurisdictionData(BaseModel):
    parent_code: str | None = None


class RoleData(BaseModel):
    description: str = ""
    business_unit_id: str | None = None
    # Epic 31, US31.6: people in the role, from the identity provider.
    members: list[str] = []


class ApprovalAuthorityData(BaseModel):
    """US27.4: one row of the approval-authority (delegation of
    authority) matrix. `limit_amount` None means "no monetary limit"."""

    role_id: str | None = None
    role_name: str = ""
    decision_type: str
    limit_amount: float | None = None
    currency: str = ""
    risk_level: str | None = None


class RegulationData(BaseModel):
    summary: str = ""
    url: str | None = None


class ObligationData(BaseModel):
    text: str = ""


class ControlData(BaseModel):
    description: str = ""
    control_type: ControlType = "other"
    requires_human_review: bool = False
    # Epic 28, US28.6: guardrails an agent touching this control must have
    # (e.g. "decision_logging", "explanation").
    required_guardrails: list[str] = []
    review_role: str | None = None
    # Epic 30 Notes: purely organizational controls ("annual training")
    # can't be exercised in a twin and don't count toward rule coverage.
    testable: bool = True


class PolicyData(BaseModel):
    summary: str = ""
    document_id: str | None = None
    effective_date: str | None = None


class GlossaryTermData(BaseModel):
    definition: str = ""
    synonyms: list[str] = []


EndpointEnvironment = Literal["sandbox", "test", "dev", "staging", "mock", "production"]
Criticality = Literal["low", "medium", "high", "critical"]


class ApplicationEndpoint(BaseModel):
    """Epic 29, US29.5: a registered endpoint. Every endpoint carries an
    explicit environment tag; only non-production ones are ever callable
    from a twin run (app/catalog/endpoints.py)."""

    url: str
    environment: EndpointEnvironment
    description: str = ""


class ApiOperation(BaseModel):
    """US29.4: one operation parsed from an attached OpenAPI document."""

    operation_id: str
    method: str
    path: str
    summary: str = ""
    parameters: dict[str, Any] = {}
    response_schema: dict[str, Any] = {}


class ApplicationData(BaseModel):
    """Epic 29, US29.1 (introduced in Epic 27 so the profile wizard could
    record the starter application list)."""

    aliases: list[str] = []
    description: str = ""
    owner: str = ""
    vendor: str = ""
    owning_business_unit_id: str | None = None
    hosting_region: str = ""
    criticality: Criticality | None = None
    # Data asset entity ids this application holds (US29.6).
    data_assets: list[str] = []
    data_classes: list[str] = []
    api_spec: dict[str, Any] | None = None
    api_title: str = ""
    api_version: str = ""
    api_operations: list[ApiOperation] = []
    endpoints: list[ApplicationEndpoint] = []
    environments: list[str] = []


Classification = Literal["Public", "Internal", "Confidential", "Confidential-PII", "Restricted"]


class DataAssetData(BaseModel):
    """Epic 29, US29.6: a business data asset (Customer, Account, Payment,
    Employee...) with its classification and residency."""

    classification: Classification = "Internal"
    residency: list[str] = []
    description: str = ""


class DataClassData(BaseModel):
    """Epic 29, US29.5."""

    description: str = ""
    sensitivity: int = Field(default=1, ge=0, le=5)
    residency: list[str] = []
    default_permission: Literal["read", "write", "deny"] = "read"


DATA_MODELS: dict[str, type[BaseModel]] = {
    "business_unit": BusinessUnitData,
    "jurisdiction": JurisdictionData,
    "role": RoleData,
    "approval_authority": ApprovalAuthorityData,
    "regulation": RegulationData,
    "obligation": ObligationData,
    "control": ControlData,
    "policy": PolicyData,
    "glossary_term": GlossaryTermData,
    "application": ApplicationData,
    "data_class": DataClassData,
    "data_asset": DataAssetData,
}


# ---- envelope -------------------------------------------------------------


class ContextEntityCreate(BaseModel):
    kind: ContextKind
    name: str
    code: str | None = None
    data: dict[str, Any] = {}
    applies_to: AppliesTo = AppliesTo()
    parent_id: str | None = None
    owner_business_unit_id: str | None = None


class ContextEntityUpdate(BaseModel):
    name: str | None = None
    code: str | None = None
    data: dict[str, Any] | None = None
    applies_to: AppliesTo | None = None
    parent_id: str | None = None
    owner_business_unit_id: str | None = None
    change_note: str | None = None


class ContextEntity(BaseModel):
    id: str
    kind: ContextKind
    name: str
    code: str | None = None
    data: dict[str, Any] = {}
    applies_to: AppliesTo = AppliesTo()
    status: ContextStatus
    confidence: float | None = None
    citations: list[Citation] = []
    source: str
    parent_id: str | None = None
    supersedes_id: str | None = None
    relaxes: bool = False
    owner_business_unit_id: str | None = None
    version: int
    created_at: datetime
    updated_at: datetime
    confirmed_by: str | None = None
    confirmed_at: datetime | None = None


class ContextEntityVersion(BaseModel):
    entity_id: str
    version: int
    snapshot: dict[str, Any]
    change_note: str | None = None
    changed_at: datetime
    changed_by: str | None = None


class ConfirmRequest(BaseModel):
    """US27.6 Notes: confirming a proposal that loosens an existing
    control must say so explicitly."""

    acknowledge_relaxation: bool = False


class BulkConfirmRequest(BaseModel):
    ids: list[str] | None = None
    min_confidence: float = Field(default=0.7, ge=0, le=1)


class BulkConfirmResult(BaseModel):
    confirmed: list[str] = []
    skipped: dict[str, str] = {}


# ---- organization ---------------------------------------------------------


class Organization(BaseModel):
    id: str
    name: str
    industry: str | None = None
    size: str | None = None
    profile: dict[str, Any] = {}
    applied_packs: dict[str, str] = {}
    created_at: datetime
    updated_at: datetime


class OrganizationUpdate(BaseModel):
    name: str
    industry: str | None = None
    size: str | None = None


class ApprovalRuleAnswer(BaseModel):
    role: str
    decision_type: str
    limit_amount: float | None = None
    currency: str = ""


class OrganizationProfileAnswers(BaseModel):
    """US27.3: the wizard's questionnaire."""

    name: str
    industry: str | None = None
    size: str | None = None
    countries: list[str] = []
    business_units: list[str] = []
    applications: list[str] = []
    approval_rules: list[ApprovalRuleAnswer] = []
    apply_packs: list[str] = []


class ProfileResult(BaseModel):
    organization: Organization
    created: dict[str, int] = {}
    applied_packs: list[str] = []
    suggested_packs: list[str] = []


# ---- starter packs --------------------------------------------------------


class StarterPackSummary(BaseModel):
    id: str
    version: str
    title: str
    description: str
    disclaimer: str
    industries: list[str] = []
    jurisdictions: list[str] = []
    obligation_count: int = 0
    control_count: int = 0
    applied_version: str | None = None
    suggested: bool = False


class StarterPackApplyResult(BaseModel):
    pack_id: str
    version: str
    created: int = 0
    updated: int = 0
    unchanged: int = 0


# ---- policy documents -----------------------------------------------------

ContextDocumentStatus = Literal["pending", "processing", "done", "failed"]


class ContextDocument(BaseModel):
    id: str
    filename: str
    content_type: str
    size_bytes: int
    status: ContextDocumentStatus
    error_message: str | None = None
    policy_entity_id: str | None = None
    proposal_count: int = 0
    created_at: datetime


class PolicyPassage(BaseModel):
    id: str
    document_id: str | None = None
    policy_entity_id: str | None = None
    policy_version: int
    clause_ref: str
    text: str
    applies_to: AppliesTo = AppliesTo()
    current: bool = True


# ---- scope / browser ------------------------------------------------------


class ProcessScope(BaseModel):
    """US27.2."""

    business_unit_id: str | None = None
    jurisdictions: list[str] = []
    process_type: str | None = None
    data_classes: list[str] = []


class ApplicableContext(BaseModel):
    """US27.9: what applies to one scope, grouped by kind."""

    scope: ProcessScope
    entities: dict[str, list[ContextEntity]] = {}


# ---- context packs (Epic 28) ----------------------------------------------


class ContextPackEntry(BaseModel):
    label: str
    entity_id: str
    kind: ContextKind
    code: str | None = None
    name: str
    version: int
    text: str = ""


class ContextPackPassage(BaseModel):
    label: str
    passage_id: str
    policy_entity_id: str | None = None
    policy_version: int
    clause_ref: str
    text: str
    score: float | None = None


class StaleEntry(BaseModel):
    """US28.8: one pinned entry whose entity has moved on since."""

    entity_id: str
    kind: str
    name: str
    pinned_version: int
    current_version: int | None = None
    current_status: str | None = None


class ContextPack(BaseModel):
    id: str
    purpose: str
    scope: ProcessScope
    process_id: str | None = None
    entries: list[ContextPackEntry] = []
    passages: list[ContextPackPassage] = []
    token_estimate: int
    budget: int
    truncated: int = 0
    created_at: datetime
    stale_entries: list[StaleEntry] = []
    rendered: str = ""


class ContextImpact(BaseModel):
    """Epic 28, US28.8 / Epic 31, US31.9: what a change to one entity
    affects."""

    entity_id: str
    entity_name: str
    pack_ids: list[str] = []
    blueprint_process_ids: list[str] = []
    agent_artifact_ids: list[str] = []
    regression_suite_ids: list[str] = []
    summary: str = ""


# ---- application catalog (Epic 29) ----------------------------------------


class SystemResolution(BaseModel):
    """US29.3: how one free-text system name maps to the catalog."""

    system_name: str
    status: Literal["linked", "matched", "ambiguous", "unmatched"]
    application_id: str | None = None
    application_name: str | None = None
    auto: bool = False
    candidates: list[dict[str, str]] = []
    used_in: list[str] = []


class SystemLinkRequest(BaseModel):
    application_id: str | None = None


class ApplicationImportRow(BaseModel):
    row: int
    name: str
    aliases: list[str] = []
    action: Literal["create", "update", "skip", "error"]
    matched_application_id: str | None = None
    message: str = ""


class ApplicationImportResult(BaseModel):
    dry_run: bool
    rows: list[ApplicationImportRow] = []
    created: int = 0
    updated: int = 0


class ApiSpecResult(BaseModel):
    application_id: str
    title: str
    version: str
    operations: list[ApiOperation] = []


class ThirdPartyRegisterRow(BaseModel):
    """US29.8."""

    application_id: str
    application: str
    vendor: str = ""
    criticality: str | None = None
    hosting_region: str = ""
    classifications: list[str] = []
    agents: list[dict[str, str]] = []
    processes: list[dict[str, str]] = []
