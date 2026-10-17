"""Epic 39: the agent runtime's API shapes."""

from __future__ import annotations

from datetime import datetime
from typing import Any

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


class Deployment(DeploymentSummary):
    identity: AgentIdentity
    bindings: list[AdapterBinding]
    contracts: list[str]
    readiness: list[str]
    shadow_waiver: str | None = None


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
    input: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = []
    steps: list[RunStep] = []


class ExecutionIn(BaseModel):
    input: dict[str, Any]
    callback_url: str | None = None


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
