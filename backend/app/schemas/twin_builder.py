"""Epics 24/25: request/response shapes for the guided Digital Twin test
builder -- the blueprint-derived context a business user builds tests
from, LLM-drafted situations, and generated test data / reviewer
personas. Nothing here is persisted directly: a confirmed situation is
saved as an ordinary TwinScenario (with its `story`), generated records
and personas are saved onto a TwinEnvironment's `datasets`/`personas`.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from .blueprint import AgentIOField
from .twin import CoverageTarget, ReviewerSetup, ScenarioStory


class GatewayBranch(BaseModel):
    target_id: str
    target_label: str
    condition: str | None = None


class GatewayContext(BaseModel):
    id: str
    label: str
    branches: list[GatewayBranch] = []


class RuleTarget(BaseModel):
    """Epic 30, US30.1: an applicable, testable control the agent must
    obey -- the source of a "Rules" coverage target."""

    id: str  # coverage target id, "ctl:<entity id>"
    entity_id: str
    version: int
    code: str | None = None
    name: str
    description: str = ""
    requires_human_review: bool = False
    review_role: str | None = None
    required_guardrails: list[str] = []


class AuthorityInfo(BaseModel):
    """Epic 30, US30.3: one row of the approval-authority matrix."""

    role: str
    decision_type: str
    limit_amount: float | None = None
    currency: str = ""


class TestBuilderContext(BaseModel):
    """Everything the guided builder needs to know about one agent,
    derived deterministically from its artifact and the baseline BPMN it
    was generated from -- no LLM involved."""

    __test__ = False  # "Test..." name -- not a pytest test class

    artifact_id: str
    agent_name: str
    purpose: str
    trigger: str
    step_labels: list[str] = []
    input_fields: list[AgentIOField] = []
    output_fields: list[AgentIOField] = []
    systems: list[str] = []
    human_checkpoint: str = "none"
    success_criteria: str = ""
    completion_conditions: str = ""
    gateways: list[GatewayContext] = []
    coverage_targets: list[CoverageTarget] = []
    # Epic 30.
    rules: list[RuleTarget] = []
    approval_authorities: list[AuthorityInfo] = []
    jurisdictions: list[str] = []
    data_classifications: list[str] = []


class Situation(BaseModel):
    """One proposed test situation: the business-readable story plus the
    start-event inputs it runs with. The frontend compiles this into a
    TwinScenarioCreate once the user confirms it."""

    story: ScenarioStory
    inputs: dict[str, Any] = {}


class SuggestSituationsRequest(BaseModel):
    count: int = Field(default=6, ge=1, le=12)
    focus: str | None = None
    # Coverage target ids already covered by saved scenarios -- the LLM is
    # asked to prioritise the rest.
    already_covered: list[str] = []


class DescribeSituationRequest(BaseModel):
    description: str = Field(min_length=3, max_length=2000)


class SituationList(BaseModel):
    situations: list[Situation]


# Epic 30, US30.4: "near_miss" -- records built to trip screening controls
# (names one letter off a listed party, amounts just over a limit).
DataMode = Literal["typical", "edge", "messy", "describe", "mutate", "clone", "near_miss"]


class GenerateDataRequest(BaseModel):
    mode: DataMode
    count: int = Field(default=5, ge=1, le=25)
    prompt: str | None = Field(default=None, max_length=2000)
    seed_record: dict[str, Any] | None = None
    sample_text: str | None = Field(default=None, max_length=20000)


class GeneratedRecord(BaseModel):
    label: str
    note: str = ""
    record: dict[str, Any]


class GeneratedRecords(BaseModel):
    records: list[GeneratedRecord]


class GeneratePersonasRequest(BaseModel):
    count: int = Field(default=3, ge=1, le=6)
    prompt: str | None = Field(default=None, max_length=2000)


class ReviewerPersona(BaseModel):
    name: str
    role: str
    description: str
    reviewer: ReviewerSetup


class GeneratedPersonas(BaseModel):
    personas: list[ReviewerPersona]
