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


DataMode = Literal["typical", "edge", "messy", "describe", "mutate", "clone"]


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
