"""Epic 16: process-level orchestration rehearsal -- runs every automatable
node's generated agent artifact (Epic 12) end to end along the finalized
BPMN graph (the same `FlowNodeInfo` graph Epic 7's blueprint evaluation
reads, app/bpmn/nodes.py), handing data off between agents, so an
Automation Architect can watch the whole blueprint execute together instead
of one agent at a time (Epic 14). See
planning/epics/16-orchestration-rehearsal.md for the full design.

Builds directly on Epic 14's schemas (app/schemas/twin.py) for per-node
simulation config/trace shapes -- this module only adds the graph-walk
layer around them: scenario config keyed by node_id (gateway decisions,
manual-node resolution, per-node system stubs/checkpoints), the per-node
run record, and the top-level run.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel

from .run import RunBase, RunStatus, RunStep
from .twin import TwinHumanCheckpointConfig, TwinSystemStub

NodeRunKind = Literal["agent", "manual", "gateway", "start_event", "end_event"]
ManualNodeMode = Literal["human_checkpoint", "fixed_stub"]
DataMappingMode = Literal["llm_adapter", "exact_field_contract"]


class ManualNodeConfig(BaseModel):
    """US16.4: how a non-automatable node is simulated during a rehearsal
    run. Default (`human_checkpoint`) reuses Epic 14's approve/reject
    simulation -- most manual steps in a real process are a decision gate,
    so that's the more realistic default. `fixed_stub` is the explicit
    opt-out for a manual step that's really just a data-producing action
    with no real decision in it."""

    mode: ManualNodeMode = "human_checkpoint"
    human_checkpoint_config: TwinHumanCheckpointConfig = TwinHumanCheckpointConfig()
    fixed_output: dict[str, Any] = {}


class GatewayDecision(BaseModel):
    """US16.2: which outgoing sequence flow to follow at a gateway node --
    required because gateway conditions are free text today, not a
    structured rule an orchestrator could evaluate (see Epic 14's
    Discovery section). `to_node_id` must be one of that gateway's actual
    successor node ids, checked at run time."""

    to_node_id: str


class OrchestrationScenarioCreate(BaseModel):
    name: str
    inputs: dict[str, Any] = {}
    gateway_decisions: dict[str, GatewayDecision] = {}
    manual_node_config: dict[str, ManualNodeConfig] = {}
    # node_id -> system_name -> stub (one automatable node's agent may need
    # several systems stubbed, same shape as Epic 14's TwinScenario but
    # nested one level deeper since a rehearsal spans many agents at once).
    system_stubs: dict[str, dict[str, TwinSystemStub]] = {}
    # node_id -> that node's own agent's human-checkpoint config, if it has
    # one -- defaults to always-approve when a node isn't listed here.
    human_checkpoint_config: dict[str, TwinHumanCheckpointConfig] = {}
    # node_id -> mode for the handoff INTO that node (i.e. keyed by the
    # receiving node, since that's the side whose input_schema defines what
    # "correct" means). Missing entries default to "llm_adapter" (US16.5).
    data_mapping_mode: dict[str, DataMappingMode] = {}
    expected_path: list[str] = []
    expected_final_output: dict[str, Any] = {}


class OrchestrationScenario(OrchestrationScenarioCreate):
    id: str
    process_id: str
    baseline_version_id: str
    created_at: datetime
    created_by: str | None = None
    created_by_name: str | None = None


class NodeRun(BaseModel):
    """Epic 17: a per-node summary within one orchestration run -- kept as
    a convenience index over the run's unified `steps` (app/schemas/run.py),
    not a second trace format: `steps` here is exactly the subsequence of
    the parent OrchestrationRun's `steps` tagged with this `node_id`.
    `status` reuses RunBase's own RunStatus rather than a bespoke two-value
    enum -- for an agent node, it's literally that agent's own nested run
    outcome (app/twin/engine.py's RunOutcome.status)."""

    node_id: str
    node_label: str
    kind: NodeRunKind
    status: RunStatus
    agent_artifact_id: str | None = None
    steps: list[RunStep] = []
    output: dict[str, Any] | None = None
    error_message: str | None = None
    total_cost_usd: float | None = None
    total_tokens: int = 0
    started_at: datetime
    completed_at: datetime | None = None


class OrchestrationRun(RunBase):
    """Epic 17: an orchestration run is now one `kind="orchestration"`
    SimulationRun -- extends RunBase with the fields specific to a
    multi-agent process walk. `handoffs` (Epic 16's DataHandoff) is gone --
    cross-agent data reshaping is now recorded as the `arguments`/`result`
    of each AGENT_DELEGATED step in `steps` instead of a second parallel
    record."""

    scenario_id: str
    process_id: str
    node_runs: list[NodeRun] = []
    visited_path: list[str] = []
