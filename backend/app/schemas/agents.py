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


class AgentArtifact(BaseModel):
    id: str
    process_id: str
    group_key: str
    node_ids: list[str]
    primary_node_id: str
    status: AgentArtifactStatus
    definition: AgentDefinition
    baseline_version_id: str
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
