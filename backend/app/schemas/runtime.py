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
    # US39.13: a canary's share of the agent's cases, when shadow began, and
    # the version it would replace.
    traffic_share: int | None = None
    shadow_started_at: datetime | None = None
    previous_deployment_id: str | None = None


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
    # US39.14/US39.15
    telemetry: dict[str, Any] = {}
    alert_rules: list[dict[str, Any]] = []
    drift_rule: dict[str, Any] = {}
    has_baseline: bool = False


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
    # US39.13: all of the agent's cases, a canary share of them, or a copy
    # of them in shadow.
    rollout: Literal["full", "canary", "shadow"] = "full"
    share: int | None = Field(default=None, ge=1, le=99)


class PromoteIn(BaseModel):
    share: int = Field(ge=1, le=100)


class RollbackIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


class MonitoringIn(BaseModel):
    """US39.14/US39.15: given fields replace what's there."""

    alert_rules: list[dict[str, Any]] | None = None
    telemetry: dict[str, Any] | None = None
    drift: dict[str, Any] | None = None


class OutcomeIn(BaseModel):
    """US39.15: what really happened to a case, from the caller's system --
    e.g. {"decision": "approve", "accountId": "ACC-7"}."""

    outcome: dict[str, Any]


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
    trigger: str = "api"
    shadow_of: str | None = None


class Execution(ExecutionSummary):
    identity_id: str
    client_id: str | None = None
    requested_by: str | None = None
    input: dict[str, Any] | None = None
    final_output: dict[str, Any] | None = None
    deviations: list[TwinDeviation] = []
    resolution: dict[str, Any] | None = None
    reported_outcome: dict[str, Any] | None = None
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
    # Set for a client that signs in with the tenant's IdP (client
    # credentials); None for one that holds a bearer token.
    oauth_client_id: str | None = None
    oauth_audience: str | None = None
    # None: may start any of the tenant's deployments.
    deployment_ids: list[str] | None = None
    created_at: datetime
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None


class RuntimeClientIn(BaseModel):
    label: str = Field(min_length=1, max_length=200)
    deployment_ids: list[str] | None = None
    # The client id the tenant's IdP issues tokens to. Without one, the
    # client gets a bearer token instead.
    oauth_client_id: str | None = Field(default=None, min_length=1, max_length=255)
    # Defaults to RUNTIME_OAUTH_AUDIENCE.
    oauth_audience: str | None = Field(default=None, min_length=1, max_length=255)


class RuntimeClientCreated(RuntimeClient):
    """Its secrets are shown once: the bearer token (None for an OAuth
    client, whose secret stays at the IdP) and the callback signing secret."""

    token: str | None = None
    signing_secret: str


class RuntimeDeploymentView(Deployment):
    """A row of the Runtime page (US39.14)."""

    process_name: str
    agent_artifact_id: str


class RuntimeOverviewRow(BaseModel):
    deployment: RuntimeDeploymentView
    metrics: dict[str, Any]
    drift: dict[str, Any] | None = None
    alerts: list[dict[str, Any]] = []


class AddedRows(BaseModel):
    added: int


class EventSubscriptionIn(BaseModel):
    """US39.16: how the tenant's system signs its events and how an event
    maps onto the agent's input ({input field: dotted path})."""

    label: str = Field(min_length=1, max_length=200)
    secret_ref: str
    signature_header: str = Field(default="X-Signature", min_length=1, max_length=100)
    signature_prefix: str = Field(default="sha256=", max_length=40)
    timestamp_header: str = Field(default="X-Timestamp", min_length=1, max_length=100)
    event_id_path: str = Field(default="id", min_length=1, max_length=200)
    input_mapping: dict[str, str]
    event_filter: dict[str, Any] = {}


class EventSubscription(EventSubscriptionIn):
    id: str
    deployment_id: str
    url: str
    created_at: datetime
    last_event_at: datetime | None = None
    revoked_at: datetime | None = None


class ReportsIn(BaseModel):
    """US39.18: executions that ran on the customer's own platform -- each
    {id, status, started_at, completed_at, input, final_output,
    total_cost_usd, total_tokens, tool_calls: [{system, failed}], outcome}."""

    executions: list[dict[str, Any]] = Field(max_length=500)


class ReportsResult(BaseModel):
    accepted: int
    duplicates: int
