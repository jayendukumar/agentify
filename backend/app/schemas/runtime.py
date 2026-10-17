"""Epic 39: the agent runtime's API shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from .run import RunStep, TwinDeviation


class AgentIdentity(BaseModel):
    id: str
    display_name: str
    status: str
    directory_client_id: str | None = None
    created_at: datetime
    revoked_at: datetime | None = None


class AdapterBinding(BaseModel):
    system_name: str
    application_id: str | None = None
    base_url: str
    operations: list[str]
    tested_operations: list[str]
    credential: dict[str, Any]
    differences: list[str] = []
    accepted_differences: list[str] = []


class AdapterBindingIn(BaseModel):
    base_url: str
    operations: list[str]
    credential: dict[str, Any]
    accept_differences: list[str] = []


class DeploymentSummary(BaseModel):
    id: str
    process_id: str
    agent_artifact_id: str
    agent_name: str
    artifact_version: int
    environment: str
    status: str
    created_by: str | None = None
    created_at: datetime
    approved_by: str | None = None
    activated_at: datetime | None = None
    stopped_at: datetime | None = None
    stop_reason: str | None = None
    executions: dict[str, int] = {}


class ToolCircuit(BaseModel):
    system_name: str
    state: str
    consecutive_failures: int
    opened_at: datetime | None = None
    reason: str | None = None


class Deployment(DeploymentSummary):
    identity: AgentIdentity
    bindings: list[AdapterBinding]
    contracts: list[str]
    readiness: list[str]
    shadow_waiver: str | None = None
    limits: dict[str, Any] = {}
    pause_rules: dict[str, Any] = {}
    review_sla_minutes: int = 240
    when_paused: str = "refuse"
    paused_at: datetime | None = None
    pause_reason: str | None = None
    circuits: list[ToolCircuit] = []


class DeploymentSettingsIn(BaseModel):
    """US39.9/US39.10/US39.12: an admin's changes; omitted fields stay."""

    limits: dict[str, Any] | None = None
    pause_rules: dict[str, Any] | None = None
    review_sla_minutes: int | None = Field(default=None, ge=5, le=60 * 24 * 14)
    when_paused: Literal["refuse", "queue"] | None = None


class ResumeIn(BaseModel):
    comment: str = Field(min_length=1, max_length=2000)


class ActivateIn(BaseModel):
    shadow_waiver: str | None = None


class StopIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class StopResult(BaseModel):
    deployments: int
    waiting_stopped: int
    running: int


class CredentialTest(BaseModel):
    ok: bool
    detail: str


class ExecutionSummary(BaseModel):
    id: str
    deployment_id: str
    artifact_version: int
    status: str
    attempt: int
    step_count: int
    total_cost_usd: float | None = None
    total_tokens: int = 0
    created_at: datetime
    completed_at: datetime | None = None


class Execution(ExecutionSummary):
    identity_id: str
    client_id: str | None = None
    requested_by: str | None = None
    input: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = []
    resolution: dict[str, Any] | None = None
    steps: list[RunStep] = []


class ExecutionIn(BaseModel):
    input: dict[str, Any]
    callback_url: str | None = None
    # US39.10: the person in the caller's system the case is for. They
    # can't review it.
    requested_by: str | None = Field(default=None, max_length=200)


class ReviewTask(BaseModel):
    id: str
    execution_id: str
    deployment_id: str
    process_id: str
    agent_name: str = ""
    kind: str
    status: str
    summary: str
    proposed_action: dict[str, Any] = {}
    case_input: dict[str, Any] = {}
    assignee_role: str | None = None
    level: int = 0
    due_at: datetime
    overdue: bool = False
    escalated_at: datetime | None = None
    decision: str | None = None
    outcome: dict[str, Any] | None = None
    comment: str | None = None
    decided_by: str | None = None
    decided_at: datetime | None = None
    created_at: datetime


class DecisionIn(BaseModel):
    decision: Literal["approve", "reject"]
    comment: str | None = Field(default=None, max_length=4000)


class ResolutionIn(BaseModel):
    outcome: dict[str, Any]
    comment: str | None = Field(default=None, max_length=4000)


class ToDatasetIn(BaseModel):
    dataset_id: str


class Notification(BaseModel):
    id: str
    kind: str
    message: str
    process_id: str | None = None
    deployment_id: str | None = None
    task_id: str | None = None
    created_at: datetime
    read_at: datetime | None = None


class ExecutionAccepted(BaseModel):
    """What a caller system gets back: no steps, no internals."""

    id: str
    status: str
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = []


class RuntimeClient(BaseModel):
    id: str
    label: str
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class RuntimeClientIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)


class RuntimeClientCreated(RuntimeClient):
    token: str
