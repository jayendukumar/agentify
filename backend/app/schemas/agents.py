from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

from .blueprint import AgentIOField, HumanCheckpoint

AgentArtifactStatus = Literal["generated", "stale"]

# Epic 20: agent-level (not user-level -- see app/api/deps.py's viewer/editor
# role gate for that) resource permissions, checked by the Tool Gateway
# (app/twin/gateway.py) before a tool call is allowed to run -- FR-AG-05.
PermissionAction = Literal["read", "update", "initiate", "override"]


class ResourcePermission(BaseModel):
    """One resource's allowed actions, e.g. resource="customer",
    actions={"read": True, "update": False}. An action missing from
    `actions` is treated as not granted (fail-closed), not an implicit
    allow."""

    resource: str
    actions: dict[PermissionAction, bool] = {}


class RuntimeGuardrails(BaseModel):
    """Epic 20, US20.2 (FR-AG-07): replaces the single hardcoded
    `Settings.twin_max_loop_turns` ceiling that was the only runtime limit
    enforced before this epic (app/twin/engine.py's old `max_turns`-only
    loop). Defaults match the requirements doc's own example values."""

    max_steps: int = Field(default=15, ge=1)
    max_tool_calls: int = Field(default=10, ge=1)
    max_runtime_seconds: float = Field(default=120.0, gt=0)
    max_model_calls: int = Field(default=15, ge=1)
    max_tokens: int = Field(default=50_000, ge=1)
    max_cost_usd: float = Field(default=1.00, gt=0)
    retry_max_attempts: int = Field(default=2, ge=0)
    loop_detection_enabled: bool = True


class EscalationTarget(BaseModel):
    type: Literal["human"] = "human"
    role: str = ""


class EscalationPolicy(BaseModel):
    """Epic 20, US20.3 (FR-AG-08): escalation is a first-class, declared
    outcome rather than only surfacing as an ad hoc deviation reason after
    a run fails."""

    escalate_when: list[str] = []
    escalation_target: EscalationTarget | None = None


class KnowledgeSourceRef(BaseModel):
    """Epic 20, US20.5 (FR-AG-09): a versioned knowledge source. No
    automatic derivation exists anywhere in the system yet -- the same
    honest gap Epic 14's US14.5 already flagged for baseline data -- so
    this defaults empty and is meant to be declared by an Automation
    Architect via the governance-update endpoint, not inferred."""

    id: str
    version: int
    # Epic 28, US28.6: populated from the pinned Context Pack -- the
    # policy/control/obligation this is, by name, so it reads on an agent
    # card without a lookup.
    name: str = ""
    kind: str = ""


ToolSandboxMode = Literal["proxy", "static", "api"]


class ToolContract(BaseModel):
    """Epic 20, US20.4 (FR-AG-04): a tool's logical contract, split from
    its sandbox/production adapter. `sandbox_mode` reuses Epic 14's
    Proxy/Static split (app/schemas/twin.py's SystemStubMode) and adds
    "api" as a declared-but-not-yet-built third mode -- Epic 14 already
    draws that same out-of-scope boundary. `resource`/`action` tie a tool
    call to the permissions block above -- the Tool Gateway denies a call
    whose contract's (resource, action) isn't explicitly granted."""

    system_name: str
    description: str = ""
    resource: str
    action: PermissionAction = "read"
    timeout_seconds: float = Field(default=30.0, gt=0)
    retry_max_attempts: int = Field(default=1, ge=0)
    side_effect: Literal["none", "read_only", "mutating"] = "read_only"
    sandbox_mode: ToolSandboxMode = "proxy"
    # Epic 29, US29.3/US29.7: the catalog application this system resolved
    # to, and the classifications of the data it holds.
    application_id: str | None = None
    data_classifications: list[str] = []


class ModelPolicy(BaseModel):
    """Epic 20, US20.6 (FR-AG-10): decouples an artifact from one
    hardcoded model string. `AgentDefinition.model` stays authoritative
    for execution (app/twin/engine.py calls `model=artifact.model`
    directly) -- `model_policy.model` mirrors it, so this is additive
    descriptive metadata, not a breaking rename."""

    capability: str = "reasoning"
    provider: str | None = None
    model: str
    temperature: float = Field(default=0.2, ge=0, le=2)
    fallback_enabled: bool = True


class AgentDefinition(BaseModel):
    """US12.2: a portable, provider-agnostic agent definition -- a system
    prompt plus tool/IO schemas, not a vendor-specific config. Mapped
    deterministically from a blueprint node's AgentSpec (app/agents/
    generation.py), not produced by its own LLM call.

    Epic 20 extends this with governance fields (permissions, guardrails,
    escalation policy, tool contracts, knowledge sources, model policy) --
    all additive with safe defaults so an artifact generated before Epic 20
    still round-trips through this schema unchanged. See app/twin/
    gateway.py for how an empty `tool_contracts` list means "governance not
    declared," falling back to Epic 14's original unrestricted behavior."""

    name: str
    purpose: str
    trigger: str
    system_prompt: str
    input_schema: list[AgentIOField] = []
    output_schema: list[AgentIOField] = []
    tools_systems_needed: list[str] = []
    human_checkpoint: HumanCheckpoint = "none"
    model: str

    tool_contracts: list[ToolContract] = []
    permissions: list[ResourcePermission] = []
    guardrails: RuntimeGuardrails = RuntimeGuardrails()
    escalation_policy: EscalationPolicy = EscalationPolicy()
    knowledge_sources: list[KnowledgeSourceRef] = []
    model_policy: ModelPolicy | None = None
    # Epic 28, US28.6: named guardrails applicable controls require (e.g.
    # "decision_logging", "explanation") -- behavioural rules, distinct from
    # `guardrails`' numeric runtime limits.
    policy_guardrails: list[str] = []

    # Epic 23, US23.2: two of the validation checklist's items that had no
    # existing home on the definition -- populated with a real,
    # non-placeholder default in app/agents/generation.py (same "real
    # defaults, not empty placeholders" precedent Epic 20 set for
    # guardrails/escalation_policy), editable via AgentGovernanceUpdate.
    success_criteria: str = ""
    completion_conditions: str = ""


# Epic 23, US23.1: a third, *computed* status layered on top of the two
# that already exist on an artifact -- AgentArtifactStatus above
# (generated/stale: whether the definition itself needs regenerating) and
# AgentPublicationModel's own draft/generated/published/deployed (Epic 15:
# the publish-event history). This one describes readiness to simulate and
# release, and is never stored directly except for the two states that are
# explicit human actions (RELEASE_CANDIDATE, APPROVED) -- see
# `repository._to_pydantic_agent_artifact` and the 2026-09-29 decision-log
# entry for exactly how each state is derived.
LifecycleStatus = Literal[
    "DRAFT",
    "VALIDATED",
    "SIMULATION_READY",
    "SIMULATING",
    "EVALUATED",
    "RELEASE_CANDIDATE",
    "APPROVED",
    "DEPLOYED",
]

# US23.2: a superset of a mandatory validation checklist item's possible
# keys -- computed by app/agents/validation.py::compute_checklist.
ChecklistItemKey = Literal[
    "purpose_and_trigger",
    "bounded_responsibility",
    "typed_input_output",
    "success_criteria",
    "completion_conditions",
    "tool_contracts",
    "permissions",
    "guardrails",
    "retry_policy",
    "escalation_rules",
    "human_approval_policy",
    "knowledge_sources",
    "model_policy",
    "runtime_limits",
    "audit_configuration",
    "test_scenarios",
]


class ChecklistItem(BaseModel):
    key: ChecklistItemKey
    label: str
    satisfied: bool
    detail: str = ""


class AgentArtifact(BaseModel):
    id: str
    process_id: str
    group_key: str
    node_ids: list[str]
    primary_node_id: str
    status: AgentArtifactStatus
    # Epic 19, US19.5: an incrementing version identifier, bumped by
    # `repository.generate_agent_artifact` every time an existing artifact
    # (same group_key) is regenerated -- distinct from `status` above,
    # which is a lifecycle state (generated/stale), not a counter. Runs,
    # regression suites, and version comparisons (US19.2-19.4) key off
    # this, not off `generated_at` (which a resumed/retried generation
    # could otherwise make ambiguous).
    version: int = 1
    definition: AgentDefinition
    baseline_version_id: str
    # Epic 28, US28.2/US28.8: pinned Context Pack, and whether any entry in
    # it has changed since (which also makes `status` "stale").
    context_pack_id: str | None = None
    context_stale: bool = False
    # Epic 23, US23.1/US23.2: computed on every read, never stored.
    lifecycle_status: LifecycleStatus = "DRAFT"
    checklist: list[ChecklistItem] = []
    marked_release_candidate_at: datetime | None = None
    approved_at: datetime | None = None
    approved_by: str | None = None
    approved_by_name: str | None = None
    generated_at: datetime
    generated_by: str | None = None
    generated_by_name: str | None = None


class AgentGovernanceUpdate(BaseModel):
    """Epic 20, US20.1: what an Automation Architect can adjust on an
    already-generated artifact without a full regenerate (which would
    silently reset these back to generation.py's defaults) -- editor-only
    via app/api/agents.py's governance endpoint. Every field is optional so
    a caller can update just one governance concern at a time."""

    permissions: list[ResourcePermission] | None = None
    guardrails: RuntimeGuardrails | None = None
    escalation_policy: EscalationPolicy | None = None
    tool_contracts: list[ToolContract] | None = None
    knowledge_sources: list[KnowledgeSourceRef] | None = None
    model_policy: ModelPolicy | None = None
    success_criteria: str | None = None
    completion_conditions: str | None = None
