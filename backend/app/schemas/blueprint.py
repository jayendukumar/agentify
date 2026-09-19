from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

Verdict = Literal["automatable", "partial", "not_automatable"]
StepType = Literal[
    "data_retrieval_transformation",
    "rule_based_decision",
    "document_generation",
    "communication_notification",
    "judgment_based_decision",
    "exception_handling",
    "approval_compliance_signoff",
    "physical_manual_action",
]
HumanCheckpoint = Literal["none", "review_before_action", "review_after_action", "escalation_on_exception"]


class AgentIOField(BaseModel):
    name: str
    source_or_destination: str
    format: str


class AgentSpec(BaseModel):
    name: str
    purpose: str
    trigger: str
    required_inputs: list[AgentIOField] = []
    expected_outputs: list[AgentIOField] = []
    tools_systems_needed: list[str] = []
    human_checkpoint: HumanCheckpoint = "none"
    consolidated_from_nodes: list[str] = []


class BlueprintNodeResult(BaseModel):
    node_id: str
    verdict: Verdict
    step_type: StepType
    rationale: str
    agent_spec: AgentSpec | None = None
    not_automatable_reason: str | None = None
    overridden: bool = False
    override_justification: str | None = None
    overridden_by: str | None = None
    overridden_by_name: str | None = None


class BlueprintOverlay(BaseModel):
    process_id: str
    baseline_version_id: str
    nodes: list[BlueprintNodeResult]
    generated_at: datetime


class BlueprintGenerateRequest(BaseModel):
    version_id: str | None = None  # None = latest finalized version


class BlueprintOverrideRequest(BaseModel):
    verdict: Verdict
    justification: str
