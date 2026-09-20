from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from .blueprint import AgentIOField, HumanCheckpoint

AgentArtifactStatus = Literal["generated", "stale"]


class AgentDefinition(BaseModel):
    """US12.2: a portable, provider-agnostic agent definition -- a system
    prompt plus tool/IO schemas, not a vendor-specific config. Mapped
    deterministically from a blueprint node's AgentSpec (app/agents/
    generation.py), not produced by its own LLM call."""

    name: str
    purpose: str
    trigger: str
    system_prompt: str
    input_schema: list[AgentIOField] = []
    output_schema: list[AgentIOField] = []
    tools_systems_needed: list[str] = []
    human_checkpoint: HumanCheckpoint = "none"
    model: str


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
