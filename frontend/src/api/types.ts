export interface ProcessSummary {
  id: string
  name: string
  document_count: number
  has_draft_bpmn: boolean
  finalized_version_count: number
  created_at: string
  updated_at: string
  scope?: ProcessScope
}

export type Confidence = 'high' | 'medium' | 'low'
export type ElementType = 'task' | 'decision' | 'start_event' | 'end_event' | 'intermediate_event'
export type ActorType = 'role' | 'system' | 'external_party'

export interface SourceRef {
  document_id: string
  location: string
  excerpt: string
}

export interface Actor {
  id: string
  name: string
  type: ActorType
}

export interface ProcessElement {
  id: string
  type: ElementType
  label: string
  actor_id: string | null
  inputs: string[]
  outputs: string[]
  systems_touched: string[]
  source_refs: SourceRef[]
  confidence: Confidence
}

export interface ProcessFlow {
  id: string
  from: string
  to: string
  condition: string | null
}

export interface ProcessSchema {
  process_name: string
  actors: Actor[]
  elements: ProcessElement[]
  flows: ProcessFlow[]
}

export interface ProcessDetail extends ProcessSummary {
  process_schema: ProcessSchema | null
}

export type IngestionStatus = 'queued' | 'processing' | 'done' | 'failed'

export interface DocumentSummary {
  id: string
  process_id: string
  filename: string
  content_type: string
  size_bytes: number
  status: IngestionStatus
  process_definition_confidence: number | null
  validation_message: string | null
  created_at: string
}

export interface DocumentDetail extends DocumentSummary {
  error_message: string | null
}

export type ChatIntent =
  | 'add_node'
  | 'delete_node'
  | 'rename_node'
  | 'reassign_actor'
  | 'change_type'
  | 'add_flow'
  | 'delete_flow'
  | 'reroute_flow'

export type ChatReplyKind = 'edit' | 'explain' | 'clarify'

export interface DiagramDiffOperation {
  op: 'add_element' | 'remove_element' | 'update_element' | 'add_flow' | 'remove_flow' | 'update_flow'
  element_id: string | null
  flow_id: string | null
  element: Record<string, unknown> | null
  flow: Record<string, unknown> | null
  fields: Record<string, unknown> | null
}

export interface DiagramDiff {
  intent: ChatIntent
  summary: string
  target_element_ids: string[]
  operations: DiagramDiffOperation[]
}

export interface ChatMessageResult {
  id: string
  process_id: string
  request_text: string
  selected_element_id: string | null
  kind: ChatReplyKind
  reply_text: string
  proposed_diff: DiagramDiff | null
  needs_confirmation: boolean
  applied: boolean
  declined: boolean
  created_at: string
  decided_at: string | null
}

export interface BPMNDocument {
  process_id: string
  xml: string
  low_confidence_element_ids: string[]
  validation_issues: string[]
  generated_at: string
}

export interface VersionSummary {
  id: string
  process_id: string
  label: string | null
  created_at: string
  created_by: string | null
  created_by_name: string | null
  // Epic 37, US37.4: only an approved version is the baseline.
  status: 'pending_approval' | 'approved' | 'rejected' | 'superseded'
  decided_at?: string | null
  decided_by?: string | null
  decided_by_name?: string | null
}

export interface VersionDetail extends VersionSummary {
  xml: string
}

export interface VersionDiffResult {
  from_version_id: string
  to_version_id: string
  added_element_ids: string[]
  removed_element_ids: string[]
  changed_element_ids: string[]
  labels: Record<string, string | null>
}

export type BlueprintVerdict = 'automatable' | 'partial' | 'not_automatable'
export type BlueprintStepType =
  | 'data_retrieval_transformation'
  | 'rule_based_decision'
  | 'document_generation'
  | 'communication_notification'
  | 'judgment_based_decision'
  | 'exception_handling'
  | 'approval_compliance_signoff'
  | 'physical_manual_action'
export type HumanCheckpoint = 'none' | 'review_before_action' | 'review_after_action' | 'escalation_on_exception'

export interface AgentIOField {
  name: string
  source_or_destination: string
  format: string
}

export interface AgentSpec {
  name: string
  purpose: string
  trigger: string
  required_inputs: AgentIOField[]
  expected_outputs: AgentIOField[]
  tools_systems_needed: string[]
  human_checkpoint: HumanCheckpoint
  consolidated_from_nodes: string[]
}

export interface BlueprintNodeResult {
  node_id: string
  verdict: BlueprintVerdict
  step_type: BlueprintStepType
  rationale: string
  agent_spec: AgentSpec | null
  not_automatable_reason: string | null
  overridden: boolean
  override_justification: string | null
  overridden_by: string | null
  overridden_by_name: string | null
  // Epic 28, US28.3/US28.5
  applicable_rules?: string[]
  citations?: Citation[]
}

export interface BlueprintOverlay {
  process_id: string
  baseline_version_id: string
  nodes: BlueprintNodeResult[]
  generated_at: string
  // Epic 28, US28.2/US28.8
  context_pack_id?: string | null
  context_stale?: boolean
}

// Epic 12
export type AgentArtifactStatus = 'generated' | 'stale'

// Epic 20: agent-level governance -- see planning/epics/20-agent-definition-governance.md
export type PermissionAction = 'read' | 'update' | 'initiate' | 'override'

export interface ResourcePermission {
  resource: string
  actions: Partial<Record<PermissionAction, boolean>>
}

export interface RuntimeGuardrails {
  max_steps: number
  max_tool_calls: number
  max_runtime_seconds: number
  max_model_calls: number
  max_tokens: number
  max_cost_usd: number
  retry_max_attempts: number
  loop_detection_enabled: boolean
}

export interface EscalationTarget {
  type: 'human'
  role: string
}

export interface EscalationPolicy {
  escalate_when: string[]
  escalation_target: EscalationTarget | null
}

export interface KnowledgeSourceRef {
  id: string
  version: number
  // Epic 28, US28.6
  name?: string
  kind?: string
}

export type ToolSandboxMode = 'proxy' | 'static' | 'api'

export interface ToolContract {
  system_name: string
  description: string
  resource: string
  action: PermissionAction
  timeout_seconds: number
  retry_max_attempts: number
  side_effect: 'none' | 'read_only' | 'mutating'
  sandbox_mode: ToolSandboxMode
}

export interface ModelPolicy {
  capability: string
  provider: string | null
  model: string
  temperature: number
  fallback_enabled: boolean
}

export interface AgentDefinition {
  name: string
  purpose: string
  trigger: string
  system_prompt: string
  input_schema: AgentIOField[]
  output_schema: AgentIOField[]
  tools_systems_needed: string[]
  human_checkpoint: HumanCheckpoint
  model: string
  tool_contracts: ToolContract[]
  permissions: ResourcePermission[]
  guardrails: RuntimeGuardrails
  escalation_policy: EscalationPolicy
  knowledge_sources: KnowledgeSourceRef[]
  model_policy: ModelPolicy | null
  // Epic 23, US23.2
  success_criteria: string
  completion_conditions: string
  // Epic 28, US28.6
  policy_guardrails?: string[]
}

export interface AgentGovernanceUpdate {
  permissions?: ResourcePermission[]
  guardrails?: RuntimeGuardrails
  escalation_policy?: EscalationPolicy
  tool_contracts?: ToolContract[]
  knowledge_sources?: KnowledgeSourceRef[]
  model_policy?: ModelPolicy
  success_criteria?: string
  completion_conditions?: string
}

// Epic 23: the DRAFT..DEPLOYED lifecycle, computed on every read except
// for the two states that are explicit human actions (RELEASE_CANDIDATE,
// APPROVED) -- see backend/app/schemas/agents.py's LifecycleStatus
// docstring. Distinct from AgentLifecycleStatus below (Epic 15's
// generated/published/deployed publish-event history) and
// AgentArtifactStatus above (Epic 12's generated/stale definition-drift
// flag) -- three different concerns layered on the same artifact.
export type LifecycleStatus =
  | 'DRAFT'
  | 'VALIDATED'
  | 'SIMULATION_READY'
  | 'SIMULATING'
  | 'EVALUATED'
  | 'RELEASE_CANDIDATE'
  | 'APPROVED'
  | 'DEPLOYED'

export type ChecklistItemKey =
  | 'purpose_and_trigger'
  | 'bounded_responsibility'
  | 'typed_input_output'
  | 'success_criteria'
  | 'completion_conditions'
  | 'tool_contracts'
  | 'permissions'
  | 'guardrails'
  | 'retry_policy'
  | 'escalation_rules'
  | 'human_approval_policy'
  | 'knowledge_sources'
  | 'model_policy'
  | 'runtime_limits'
  | 'audit_configuration'
  | 'test_scenarios'

export interface ChecklistItem {
  key: ChecklistItemKey
  label: string
  satisfied: boolean
  detail: string
}

export interface AgentArtifact {
  id: string
  process_id: string
  group_key: string
  node_ids: string[]
  primary_node_id: string
  status: AgentArtifactStatus
  // Epic 19, US19.5: bumped every time this artifact is regenerated --
  // distinct from `status` above (a lifecycle state, not a counter).
  version: number
  definition: AgentDefinition
  baseline_version_id: string
  // Epic 28, US28.2/US28.8
  context_pack_id?: string | null
  context_stale?: boolean
  // Epic 23, US23.1/US23.2
  lifecycle_status: LifecycleStatus
  checklist: ChecklistItem[]
  marked_release_candidate_at: string | null
  approved_at: string | null
  approved_by: string | null
  approved_by_name: string | null
  generated_at: string
  generated_by: string | null
  generated_by_name: string | null
}

export type GapKind = 'structural' | 'cross_document' | 'policy'
export type GapFindingStatus = 'open' | 'resolved' | 'dismissed'

export interface GapFindingOption {
  label: string
  diff: DiagramDiff | null
}

export interface GapFinding {
  id: string
  process_id: string
  kind: GapKind
  question: string
  target_element_ids: string[]
  options: GapFindingOption[]
  status: GapFindingStatus
  chosen_option_label: string | null
  created_at: string
  decided_at: string | null
  decided_by: string | null
  decided_by_name: string | null
  // Epic 28, US28.4
  citations?: Citation[]
  context_pack_id?: string | null
}

// Epic 13
export interface RegistryStatus {
  name: string
  type: string
  reachable: boolean
  authenticated: boolean
  message: string | null
}

export interface RegistryEntry {
  id: string
  registry_name: string
  agent_name: string
  tags: string[]
  definition: Record<string, unknown>
  pushed_at: string
  source_process_id: string | null
  source_node_ids: string[]
  pushed_by: string | null
  pushed_by_name: string | null
}

// Epic 17: the unified run state machine and typed execution-step taxonomy
// shared by twin (Epic 14) and orchestration (Epic 16) runs -- see
// backend/app/schemas/run.py. `RunStatus` describes execution/lifecycle
// outcome only, never scenario grading (see `graded_passed` on TwinRun/
// OrchestrationRun below).
export type RunStatus =
  | 'CREATED'
  | 'QUEUED'
  | 'RUNNING'
  | 'WAITING_FOR_MODEL'
  | 'WAITING_FOR_TOOL'
  | 'WAITING_FOR_HUMAN'
  | 'COMPLETED'
  | 'FAILED'
  | 'CANCELLED'
  | 'TIMED_OUT'
  | 'ESCALATED'

export const TERMINAL_RUN_STATUSES: ReadonlySet<RunStatus> = new Set([
  'COMPLETED',
  'FAILED',
  'CANCELLED',
  'TIMED_OUT',
  'ESCALATED',
])

export type StepType =
  | 'RUN_STARTED'
  | 'CONTEXT_RETRIEVED'
  | 'MODEL_INVOKED'
  | 'DECISION'
  | 'TOOL_REQUESTED'
  | 'POLICY_CHECK'
  | 'TOOL_EXECUTED'
  | 'TOOL_FAILED'
  | 'HUMAN_APPROVAL_REQUESTED'
  | 'HUMAN_APPROVED'
  | 'HUMAN_REJECTED'
  | 'AGENT_DELEGATED'
  | 'RETRY'
  | 'ESCALATION'
  | 'FINAL_RESPONSE'
  | 'RUN_COMPLETED'

export interface RunStep {
  seq: number
  type: StepType
  node_id: string | null
  target: string | null
  arguments: Record<string, unknown>
  result: Record<string, unknown> | null
  decision: 'approve' | 'reject' | null
  reason: string | null
  denied: boolean
  static_fallback: boolean
  // Epic 18, US18.2: LLM usage this specific step caused (a model turn, a
  // proxy-simulated tool response, a data-adapter reshape) -- null for a
  // step with no LLM call of its own, or a step mapped from a pre-Epic-18
  // historical run. Not a second source of truth for the run-level total.
  tokens: number | null
  cost_usd: number | null
  occurred_at: string
}

// Epic 19: named evaluation dimensions (US19.1), reusable regression suites
// (US19.2/US19.3), and run comparison (US19.4) -- see backend/app/schemas/
// evaluation.py. Structural, not an LLM-judge score.
export type EvaluationDimensionName =
  | 'task_completion'
  | 'policy_compliance'
  | 'tool_selection_correctness'
  | 'tool_argument_correctness'
  | 'output_correctness'
  | 'grounding'
  | 'resilience'
  | 'efficiency'
  | 'human_intervention'
  | 'latency'
  | 'token_usage'
  | 'cost'

export interface EvaluationDimension {
  name: EvaluationDimensionName
  passed: boolean
  detail: string
}

export interface EvaluationResult {
  dimensions: EvaluationDimension[]
  passed_count: number
  total_count: number
}

export interface RegressionSuiteCreate {
  name: string
  agent_artifact_id: string | null
  kind: 'twin' | 'orchestration'
  scenario_ids: string[]
}

export interface RegressionSuite extends RegressionSuiteCreate {
  id: string
  process_id: string
  created_at: string
  created_by: string | null
  created_by_name: string | null
}

export interface RegressionSuiteScenarioResult {
  scenario_id: string
  run_id: string
  status: string
  graded_passed: boolean | null
}

export interface RegressionSuiteRun {
  id: string
  suite_id: string
  status: 'running' | 'completed'
  run_ids: string[]
  pass_count: number
  total_count: number
  scenario_results: RegressionSuiteScenarioResult[]
  started_at: string
  completed_at: string | null
}

export interface RunComparison {
  run_a_id: string
  run_b_id: string
  differences: string[]
}

// Epic 21: named, versioned Digital Twin Environments (US21.1/US21.4) and
// their fault-injection catalog (US21.2) -- see backend/app/schemas/
// environment.py. A fault intercepts a call before Epic 14's Proxy/Static
// system_stubs resolution ever runs; everything else here is descriptive
// reference data with no other functional wiring in this pass.
export type FaultType =
  | 'timeout'
  | 'http_500'
  | 'http_429'
  | 'invalid_response'
  | 'slow_response'
  | 'missing_data'
  | 'duplicate_event'
  | 'out_of_order_event'
  | 'permission_denied'
  | 'dependency_unavailable'

export interface FaultConfig {
  type: FaultType
  probability: number
  config: Record<string, unknown>
}

export interface TwinEnvironmentCreate {
  name: string
  systems: string[]
  datasets: Record<string, unknown>
  personas: Record<string, unknown>
  events: Record<string, unknown>[]
  faults: Record<string, FaultConfig[]>
}

export interface TwinEnvironment extends TwinEnvironmentCreate {
  id: string
  process_id: string
  version: number
  created_at: string
  created_by: string | null
  created_by_name: string | null
  updated_at: string
}

// Epic 22: per-run execution snapshots (US22.1/US22.2) and the three fixed
// reproducibility tiers (US22.3) -- see backend/app/schemas/
// reproducibility.py. `snapshot` is deliberately untyped: a point-in-time
// capture of several other schemas' current values, not a second parallel
// type to keep in sync.
export type ReproducibilityTier = 'guaranteed' | 'best_effort' | 'not_guaranteed'

export interface RunSnapshotView {
  run_id: string
  snapshot: Record<string, unknown> | null
  reproducibility: Record<string, ReproducibilityTier>
  explanation: Record<string, string>
}

// Epic 14 (core slice)
// Epic 29, US29.5: 'api' calls the linked application's sandbox endpoint.
export type TwinSystemStubMode = 'proxy' | 'static' | 'api'
export type TwinHumanCheckpointMode = 'probability' | 'rule' | 'manual'
export type TwinRuleOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'

export interface TwinStaticResponseRule {
  match: Record<string, unknown>
  response: Record<string, unknown>
}

export interface TwinSystemStub {
  mode: TwinSystemStubMode
  static_responses: TwinStaticResponseRule[]
}

export interface TwinHumanDecisionRule {
  field: string
  operator: TwinRuleOperator
  value: unknown
  on_true: 'approve' | 'reject'
  on_false: 'approve' | 'reject'
}

export interface TwinHumanCheckpointConfig {
  mode: TwinHumanCheckpointMode
  approve_probability: number
  seed: number | null
  rule: TwinHumanDecisionRule | null
}

// Kept narrow and separate from StepType above -- this is only the
// two-kind vocabulary a scenario author uses to declare an *expected* step
// (grading), not the runtime's own full execution-trace taxonomy.
export type TwinExpectedStepKind = 'tool_call' | 'human_checkpoint'

export interface TwinExpectedStep {
  kind: TwinExpectedStepKind
  target: string | null
  expected_decision: 'approve' | 'reject' | null
}

export interface TwinScenario {
  id: string
  agent_artifact_id: string
  name: string
  inputs: Record<string, unknown>
  system_stubs: Record<string, TwinSystemStub>
  human_checkpoint_config: TwinHumanCheckpointConfig
  expected_steps: TwinExpectedStep[]
  expected_outputs: Record<string, unknown>
  // Epic 21, US21.3: optionally pins this scenario to a reusable
  // TwinEnvironment's fault catalog -- null for a scenario with no
  // environment reference (every scenario created before this epic).
  environment_id: string | null
  environment_version: number | null
  // Epic 24, US24.4/US24.6: per-scenario faults (offered before the
  // environment's), grading match mode, and the business-readable story a
  // guided-builder scenario was authored from (null for JSON-authored ones).
  faults: Record<string, FaultConfig[]>
  step_match: TwinStepMatch
  story: ScenarioStory | null
  // Epic 32, US32.2: a data-driven scenario's dataset.
  dataset_id?: string | null
  decision_field?: string
  created_at: string
  created_by: string | null
  created_by_name: string | null
}

export type TwinStepMatch = 'exact' | 'in_order'

export interface TwinScenarioCreate {
  name: string
  inputs?: Record<string, unknown>
  system_stubs?: Record<string, TwinSystemStub>
  human_checkpoint_config?: TwinHumanCheckpointConfig
  expected_steps?: TwinExpectedStep[]
  expected_outputs?: Record<string, unknown>
  environment_id?: string | null
  faults?: Record<string, FaultConfig[]>
  step_match?: TwinStepMatch
  story?: ScenarioStory | null
  // Epic 32, US32.2
  dataset_id?: string | null
  decision_field?: string
}

// Epics 24/25: the guided test builder -- see backend/app/schemas/
// twin_builder.py and twin.py's ScenarioStory.
export type CoverageKind = 'happy_path' | 'gateway_branch' | 'checkpoint' | 'system_failure' | 'data_quality' | 'custom' | 'control'
export type SystemBehaviour = 'normal' | 'down' | 'timeout' | 'denied' | 'bad_data' | 'no_record' | 'custom_data'
export type ReviewerBehaviour = 'none' | 'approve' | 'reject' | 'threshold'

export interface CoverageTarget {
  id: string
  kind: CoverageKind
  label: string
}

export interface ReviewerSetup {
  behaviour: ReviewerBehaviour
  field: string | null
  threshold: number | null
  persona_name: string | null
}

// Epic 30, US30.5
export interface ComplianceAssertion {
  control_id: string
  kind: 'escalated' | 'blocked' | 'reviewed' | 'logged'
  role: string | null
  system: string | null
  field: string | null
}

export interface SituationExpectation {
  systems_used: string[]
  review_decision: 'approve' | 'reject' | null
  output_fields: string[]
  compliance?: ComplianceAssertion[]
}

export interface ScenarioStory {
  title: string
  given: string
  when: string
  then: string
  covers: string[]
  source: 'suggested' | 'described' | 'manual'
  system_behaviour: Record<string, SystemBehaviour>
  system_data: Record<string, Record<string, unknown>>
  reviewer: ReviewerSetup
  expect: SituationExpectation
}

export interface Situation {
  story: ScenarioStory
  inputs: Record<string, unknown>
}

export interface GatewayContext {
  id: string
  label: string
  branches: { target_id: string; target_label: string; condition: string | null }[]
}

export interface TestBuilderContext {
  artifact_id: string
  agent_name: string
  purpose: string
  trigger: string
  step_labels: string[]
  input_fields: AgentIOField[]
  output_fields: AgentIOField[]
  systems: string[]
  human_checkpoint: HumanCheckpoint
  success_criteria: string
  completion_conditions: string
  gateways: GatewayContext[]
  coverage_targets: CoverageTarget[]
  // Epic 30
  rules?: { id: string; code: string | null; name: string; description: string; review_role: string | null }[]
  approval_authorities?: { role: string; decision_type: string; limit_amount: number | null; currency: string }[]
  jurisdictions?: string[]
}

export type DataMode = 'typical' | 'edge' | 'messy' | 'describe' | 'mutate' | 'clone' | 'near_miss'

export interface GeneratedRecord {
  label: string
  note: string
  record: Record<string, unknown>
}

export interface ReviewerPersona {
  name: string
  role: string
  description: string
  reviewer: ReviewerSetup
}

export interface TwinDeviation {
  reason: string
  step_index: number | null
}

export interface TwinRun {
  id: string
  kind: 'twin'
  scenario_id: string
  agent_artifact_id: string
  status: RunStatus
  steps: RunStep[]
  final_output: Record<string, unknown> | null
  deviations: TwinDeviation[]
  graded_passed: boolean | null
  // Epic 19, US19.1: null for a non-terminal or ungraded run, and for any
  // run persisted before this epic.
  evaluation: EvaluationResult | null
  total_cost_usd: number | null
  total_tokens: number
  turns_used: number
  started_at: string
  completed_at: string | null
  run_by: string | null
  run_by_name: string | null
}

// Epic 14, US14.5: explicitly user-supplied -- nothing in this system
// extracts timing/error-rate data automatically (see the epic's planning
// doc), so both fields are independent and optional.
export interface TwinBaseline {
  agent_artifact_id: string
  typical_time_seconds: number | null
  error_rate: number | null
  notes: string | null
  recorded_at: string
  recorded_by: string | null
  recorded_by_name: string | null
}

export interface TwinBaselineComparison {
  average_run_duration_seconds: number | null
  time_delta_seconds: number | null
  error_rate_delta: number | null
}

export interface TwinSummary {
  agent_artifact_id: string
  run_count: number
  pass_rate: number | null
  total_cost_usd: number | null
  average_cost_usd: number | null
  common_failure_reasons: string[]
  baseline: TwinBaseline | null
  baseline_comparison: TwinBaselineComparison | null
}

export interface RegistrySearchResult {
  entries: RegistryEntry[]
  registry_errors: Record<string, string>
}

// Epic 16: process-level orchestration rehearsal -- runs every automatable
// node's agent together along the real process graph. Builds on the Epic 14
// types above (TwinSystemStub, TwinHumanCheckpointConfig, TwinDeviation)
// rather than redefining them.
export type NodeRunKind = 'agent' | 'manual' | 'gateway' | 'start_event' | 'end_event'
export type ManualNodeMode = 'human_checkpoint' | 'fixed_stub'
export type DataMappingMode = 'llm_adapter' | 'exact_field_contract' | 'auto'
// Epic 36, US36.2/US36.3.
export type HandoffMode = 'previous_output' | 'case_file'
export type GatewayRouting = 'configured' | 'rules' | 'agent'

export interface ManualNodeConfig {
  mode: ManualNodeMode
  human_checkpoint_config: TwinHumanCheckpointConfig
  fixed_output: Record<string, unknown>
}

export interface GatewayDecision {
  to_node_id: string
}

export interface OrchestrationScenarioCreate {
  name: string
  inputs: Record<string, unknown>
  gateway_decisions: Record<string, GatewayDecision>
  manual_node_config: Record<string, ManualNodeConfig>
  system_stubs: Record<string, Record<string, TwinSystemStub>>
  human_checkpoint_config: Record<string, TwinHumanCheckpointConfig>
  data_mapping_mode: Record<string, DataMappingMode>
  expected_path: string[]
  expected_final_output: Record<string, unknown>
  // Optional: the server always derives environment_version from
  // environment_id -- a caller creating a scenario never supplies it
  // directly, though a fetched OrchestrationScenario (which extends this)
  // always has both populated.
  environment_id?: string | null
  environment_version?: number | null
  handoff?: HandoffMode
  default_gateway_routing?: GatewayRouting
  gateway_routing?: Record<string, GatewayRouting>
}

export interface OrchestrationScenario extends OrchestrationScenarioCreate {
  id: string
  process_id: string
  baseline_version_id: string
  created_at: string
  created_by: string | null
  created_by_name: string | null
}

export interface NodeRun {
  node_id: string
  node_label: string
  kind: NodeRunKind
  status: RunStatus
  agent_artifact_id: string | null
  steps: RunStep[]
  output: Record<string, unknown> | null
  error_message: string | null
  total_cost_usd: number | null
  total_tokens: number
  started_at: string
  completed_at: string | null
}

export interface OrchestrationRun {
  id: string
  kind: 'orchestration'
  scenario_id: string
  process_id: string
  status: RunStatus
  steps: RunStep[]
  node_runs: NodeRun[]
  visited_path: string[]
  final_output: Record<string, unknown> | null
  deviations: TwinDeviation[]
  graded_passed: boolean | null
  evaluation: EvaluationResult | null
  total_cost_usd: number | null
  total_tokens: number
  started_at: string
  completed_at: string | null
  run_by: string | null
  run_by_name: string | null
}

// Epic 15
export type PublicationStatus = 'published' | 'deployed'
// "draft" (no artifact generated yet) has no AgentPublishStatus to speak
// of -- the frontend renders that state itself whenever `artifact` is null,
// same as AgentArtifactActions already does for generate vs. regenerate.
export type AgentLifecycleStatus = 'generated' | 'published' | 'deployed'

export interface AgentPublication {
  id: string
  agent_artifact_id: string
  registry_name: string
  registry_entry_id: string
  version: number
  status: PublicationStatus
  published_at: string
  published_by: string | null
  published_by_name: string | null
  deployed_at: string | null
  deployed_by: string | null
  deployed_by_name: string | null
}

export interface AgentPublishStatus {
  agent_artifact_id: string
  lifecycle_status: AgentLifecycleStatus
  needs_republish: boolean
  latest_publication: AgentPublication | null
  publications: AgentPublication[]
}

// Epic 37, US37.1: roles that match the work. What a user may do is the
// permissions their roles grant (backend app/auth/permissions.py).
export type Role = 'analyst' | 'architect' | 'process_owner' | 'approver' | 'auditor' | 'admin'

export type Permission =
  | 'process.edit'
  | 'process.finalize'
  | 'process.approve'
  | 'process.delete'
  | 'blueprint.edit'
  | 'agent.build'
  | 'agent.test'
  | 'agent.release_candidate'
  | 'agent.approve'
  | 'agent.release_criteria'
  | 'agent.publish'
  | 'context.edit'
  | 'context.confirm'
  | 'catalog.edit'
  | 'org.admin'
  | 'users.manage'
  | 'audit.read'

export interface RoleGrant {
  role: Role
  scope_type: 'organization' | 'business_unit'
  scope_id: string
  scope_name?: string | null
  source?: string | null
}

export interface User {
  id: string
  name: string
  active: boolean
  roles: RoleGrant[]
  // Held at some scope -- the server still checks the scope per request.
  permissions: Permission[]
  created_at: string
  // Epic 37, US37.13: only on the Users list.
  active_sessions?: number | null
}

// US37.12: what sign-in, refresh and /api/auth/me return to the
// signed-in user -- the CSRF token writes need, and when the session ends.
export interface SignedInUser extends User {
  csrf_token: string
  session_id: string
  session_expires_at: string
  idle_timeout_minutes: number
}

// US37.13: one live sign-in.
export interface SessionInfo {
  id: string
  created_at: string
  last_used_at: string
  expires_at: string
  ip: string | null
  user_agent: string | null
  current: boolean
}

export interface RoleOption {
  value: Role
  label: string
  permissions: Permission[]
}

export interface AuthConfig {
  auth_mode: string
  environment: string
  roles: RoleOption[]
}

// Epic 37, US37.3-US37.5
export interface ChangeEntry {
  actor_id: string | null
  actor_name: string | null
  action: string
  label: string
  tier: 'high' | 'standard'
  at: string | null
}

export interface ApprovalRecord {
  approver_id: string | null
  approver_name: string | null
  decision: 'approved' | 'rejected'
  comment: string | null
  at: string | null
}

export interface ApprovalStatus {
  object_type: 'process_version' | 'agent'
  object_id: string
  status: string
  required_approvals: number
  approvals: ApprovalRecord[]
  changes: ChangeEntry[]
  can_approve: boolean
  reason: string | null
}

// Epic 37, US37.6
export interface AuditEvent {
  id: string
  at: string
  actor_type: string
  actor_id: string | null
  actor_name: string | null
  action: string
  object_type: string | null
  object_id: string | null
  process_id: string | null
  outcome: 'success' | 'refused'
  ip: string | null
  detail: Record<string, unknown>
}

export interface AuditFilters {
  actor_id?: string
  action?: string
  process_id?: string
  outcome?: 'success' | 'refused'
  since?: string
  until?: string
}

export interface GovernanceSettings {
  required_agent_approvals: number
  required_agent_approvals_by_unit: Record<string, number>
}

export interface ApiErrorBody {
  detail: string
}

// Epic 27 -- organization context
export type ContextKind =
  | 'business_unit'
  | 'jurisdiction'
  | 'role'
  | 'approval_authority'
  | 'regulation'
  | 'obligation'
  | 'control'
  | 'policy'
  | 'glossary_term'
  | 'application'
  | 'data_class'
  | 'data_asset'

export type ContextStatus = 'proposed' | 'confirmed' | 'dismissed' | 'retired'

export interface AppliesTo {
  business_units: string[]
  jurisdictions: string[]
  data_classes: string[]
  process_types: string[]
  systems: string[]
}

export interface Citation {
  passage_id: string | null
  policy_id: string | null
  clause_ref: string | null
  quote: string
}

export interface ContextEntity {
  id: string
  kind: ContextKind
  name: string
  code: string | null
  data: Record<string, unknown>
  applies_to: AppliesTo
  status: ContextStatus
  confidence: number | null
  citations: Citation[]
  source: string
  parent_id: string | null
  supersedes_id: string | null
  relaxes: boolean
  owner_business_unit_id: string | null
  version: number
  created_at: string
  updated_at: string
  confirmed_by: string | null
  confirmed_at: string | null
}

export interface ContextEntityCreate {
  kind: ContextKind
  name: string
  code?: string | null
  data?: Record<string, unknown>
  applies_to?: Partial<AppliesTo>
  parent_id?: string | null
}

export interface ContextEntityVersion {
  entity_id: string
  version: number
  snapshot: Record<string, unknown>
  change_note: string | null
  changed_at: string
  changed_by: string | null
}

export interface Organization {
  id: string
  name: string
  industry: string | null
  size: string | null
  profile: Record<string, unknown>
  applied_packs: Record<string, string>
  created_at: string
  updated_at: string
}

export interface ApprovalRuleAnswer {
  role: string
  decision_type: string
  limit_amount: number | null
  currency: string
}

export interface OrganizationProfileAnswers {
  name: string
  industry: string | null
  size: string | null
  countries: string[]
  business_units: string[]
  applications: string[]
  approval_rules: ApprovalRuleAnswer[]
  apply_packs: string[]
}

export interface ProfileResult {
  organization: Organization
  created: Record<string, number>
  applied_packs: string[]
  suggested_packs: string[]
}

export interface StarterPackSummary {
  id: string
  version: string
  title: string
  description: string
  disclaimer: string
  industries: string[]
  jurisdictions: string[]
  obligation_count: number
  control_count: number
  applied_version: string | null
  suggested: boolean
}

export interface StarterPackApplyResult {
  pack_id: string
  version: string
  created: number
  updated: number
  unchanged: number
}

export interface ContextDocument {
  id: string
  filename: string
  content_type: string
  size_bytes: number
  status: 'pending' | 'processing' | 'done' | 'failed'
  error_message: string | null
  policy_entity_id: string | null
  proposal_count: number
  created_at: string
}

export interface PolicyPassage {
  id: string
  document_id: string | null
  policy_entity_id: string | null
  policy_version: number
  clause_ref: string
  text: string
  applies_to: AppliesTo
  current: boolean
}

export interface BulkConfirmResult {
  confirmed: string[]
  skipped: Record<string, string>
}

export interface ProcessScope {
  business_unit_id: string | null
  jurisdictions: string[]
  process_type: string | null
  data_classes: string[]
}

export interface ApplicableContext {
  scope: ProcessScope
  entities: Partial<Record<ContextKind, ContextEntity[]>>
}

// Epic 28 -- context packs
export interface ContextPackEntry {
  label: string
  entity_id: string
  kind: ContextKind
  code: string | null
  name: string
  version: number
  text: string
}

export interface ContextPackPassage {
  label: string
  passage_id: string
  policy_entity_id: string | null
  policy_version: number
  clause_ref: string
  text: string
  score: number | null
}

export interface StaleEntry {
  entity_id: string
  kind: string
  name: string
  pinned_version: number
  current_version: number | null
  current_status: string | null
}

export interface ContextPack {
  id: string
  purpose: string
  scope: ProcessScope
  process_id: string | null
  entries: ContextPackEntry[]
  passages: ContextPackPassage[]
  token_estimate: number
  budget: number
  truncated: number
  created_at: string
  stale_entries: StaleEntry[]
  rendered: string
}

export interface ContextImpact {
  entity_id: string
  entity_name: string
  pack_ids: string[]
  blueprint_process_ids: string[]
  agent_artifact_ids: string[]
  regression_suite_ids: string[]
  summary: string
}

// Epic 29 -- application and data catalog
export type EndpointEnvironment = 'sandbox' | 'test' | 'dev' | 'staging' | 'mock' | 'production'
export type Classification = 'Public' | 'Internal' | 'Confidential' | 'Confidential-PII' | 'Restricted'

export interface ApplicationEndpoint {
  url: string
  environment: EndpointEnvironment
  description?: string
}

export interface ApiOperation {
  operation_id: string
  method: string
  path: string
  summary: string
}

export interface SystemResolution {
  system_name: string
  status: 'linked' | 'matched' | 'ambiguous' | 'unmatched'
  application_id: string | null
  application_name: string | null
  auto: boolean
  candidates: { id: string; name: string }[]
  used_in: string[]
}

export interface ApplicationImportRow {
  row: number
  name: string
  aliases: string[]
  action: 'create' | 'update' | 'skip' | 'error'
  matched_application_id: string | null
  message: string
}

export interface ApplicationImportResult {
  dry_run: boolean
  rows: ApplicationImportRow[]
  created: number
  updated: number
}

export interface ApiSpecResult {
  application_id: string
  title: string
  version: string
  operations: ApiOperation[]
}

export interface ThirdPartyRegisterRow {
  application_id: string
  application: string
  vendor: string
  criticality: string | null
  hosting_region: string
  classifications: string[]
  agents: { id: string; name: string; process_id: string }[]
  processes: { id: string; name: string }[]
}

// Epic 30 -- compliance evidence
export interface SituationEvidence {
  scenario_id: string
  scenario_name: string
  run_id: string | null
  run_at: string | null
  passed: boolean | null
  agent_version: number | null
  context_pack_id: string | null
  current: boolean
}

export interface ControlEvidence {
  target_id: string
  control_id: string
  code: string | null
  name: string
  version: number
  status: 'evidenced' | 'out_of_date' | 'failing' | 'missing'
  situations: SituationEvidence[]
}

export interface ComplianceEvidence {
  artifact_id: string
  agent_name: string
  agent_version: number
  context_pack_id: string | null
  context_stale: boolean
  pinned_versions: { name: string; kind: string; version: number }[]
  controls: ControlEvidence[]
  evidenced: number
  total: number
  disclaimer: string
  generated_at: string
}

// Epic 31 -- connectors, federation, model policy, audit
export interface ConnectorConfig {
  name: string
  type: string
  config: Record<string, unknown>
  enabled: boolean
  schedule_minutes: number | null
  owner_business_unit_id: string | null
}

export interface Connector extends ConnectorConfig {
  id: string
  last_sync_at: string | null
  last_status: string | null
  last_error: string | null
}

export interface ConnectorRun {
  id: string
  status: string
  stats: Record<string, number>
  impacts: ContextImpact[]
  error: string | null
  started_at: string
  finished_at: string | null
}

export interface SyncStarted {
  queued: boolean
  job_id: string | null
  run: ConnectorRun | null
}

export interface Membership {
  business_unit_id: string
  steward: boolean
}

export interface MemberAccess {
  user_id: string
  name: string
  roles: string[]
  memberships: Membership[]
}

export interface LlmPolicy {
  allowed_providers: string[]
  residency: string[]
  base_url: string | null
  api_key_env: string | null
  provider_name: string | null
  provider_region: string | null
  model: string | null
  redact_pii: boolean
}

export interface ContextChange {
  entity_id: string
  kind: string
  name: string
  version: number
  change_note: string | null
  changed_at: string
  impact: ContextImpact
}

export interface AccessLogEntry {
  id: string
  at: string
  actor_type: string
  actor: string
  action: string
  process_id: string | null
  pack_id: string | null
  entity_ids: string[]
  business_unit_id: string | null
  detail: string | null
}

// Epic 32 -- labelled datasets
export type Provenance = 'historical' | 'synthetic' | 'expert' | 'oracle'
export type RowDecision = 'approve' | 'reject' | 'escalate'

export interface RowLabels {
  outputs: Record<string, unknown>
  decision: RowDecision | null
}

export interface DatasetRowInput {
  inputs: Record<string, unknown>
  labels?: RowLabels
  segment?: string
  provenance?: Provenance
  tags?: string[]
  critical?: boolean
  row_key?: string
}

export interface DatasetRow {
  row_key: string
  revision_id: string
  valid_from_version: number
  inputs: Record<string, unknown>
  labels: RowLabels | null
  segment: string
  provenance: Provenance
  tags: string[]
  critical: boolean
  ambiguous: boolean
  ambiguity_note: string | null
  second_labels: RowLabels | null
  holdout: boolean
  source_run_id: string | null
  confirmed_by: string | null
  confirmed_at: string | null
}

export interface DatasetSummary {
  id: string
  process_id: string
  agent_artifact_id: string | null
  name: string
  description: string
  version: number
  row_count: number
  holdout_count: number
  ambiguous_count: number
  confirmed_count: number
  segments: Record<string, number>
  provenance: Record<string, number>
  agreement_rate: number | null
  created_at: string
  updated_at: string
}

export interface DatasetRowPage {
  version: number
  total: number
  rows: DatasetRow[]
}

export interface RowChange {
  row_key: string
  change: 'added' | 'removed' | 'relabelled' | 'edited'
  before: Record<string, unknown> | null
  after: Record<string, unknown> | null
}

export interface DatasetDiff {
  from_version: number
  to_version: number
  added: number
  removed: number
  relabelled: number
  edited: number
  changes: RowChange[]
}

export type ColumnRole = 'input' | 'label' | 'decision' | 'segment' | 'tag' | 'ignore'
export type PseudonymMode = 'keep' | 'hash' | 'fake'

export interface ColumnMapping {
  column: string
  role: ColumnRole
  field: string | null
  pseudonymize: PseudonymMode
}

export interface ImportPreview {
  columns: string[]
  suggested: ColumnMapping[]
  sample: Record<string, string | null>[]
  total_rows: number
  mapped_sample: DatasetRowInput[]
}

// Epic 33 -- oracles
export interface DecisionRule {
  id: string
  gateway_id: string
  gateway_label: string
  branch_target_id: string
  branch_label: string
  condition_text: string
  expression: Record<string, unknown> | null
  rendered: string
  outcome: 'approve' | 'reject' | 'escalate' | null
  priority: number
  is_default: boolean
  status: 'proposed' | 'confirmed' | 'unstructurable'
  note: string | null
  control_id: string | null
  version: number
}

export interface OracleLabelResult {
  version: number
  labelled: number
  agreed: number
  conflicts: number
  undetermined: number
}

export interface OracleConflict {
  row_key: string
  inputs: Record<string, unknown>
  label: string | null
  label_provenance: string
  oracle: string
  fired_rules: string[]
}

export interface GenerateResult {
  requested: number
  kept: number
  discarded: number
  by_decision: Record<string, number>
  version: number
}

export interface MetamorphicRelation {
  id: string
  dataset_id: string
  name: string
  kind: 'invariance' | 'directional'
  field: string
  perturbation: Record<string, unknown>
  direction: string | null
}

export interface ReviewItem {
  row_key: string
  inputs: Record<string, unknown>
  label: string | null
  oracle: string | null
  agent: string | null
  critical: boolean
  disagreements: string[]
  score: number
}

export interface JudgeRubric {
  id: string
  name: string
  field: string
  rubric: string
  judge_model: string | null
  min_agreement: number
  min_samples: number
  version: number
  calibration: { samples: number; agreement: number | null; kappa: number | null; gate_eligible: boolean } | null
  gate_eligible: boolean
}

// Epic 34 -- batches
export type BatchTier = 'smoke' | 'regression' | 'full' | 'custom'

export interface BatchRequest {
  tier: BatchTier
  sample_size?: number | null
  sample_fraction?: number | null
  trials: number
  budget_usd?: number | null
  mode: 'correctness' | 'load'
  target_concurrency?: number
  stub_latency_ms?: number
}

export interface BatchEstimate {
  rows: number
  trials: number
  runs: number
  cost_per_run_usd: number
  seconds_per_run: number
  total_cost_usd: number
  total_minutes: number
  basis: string
}

export interface BatchView {
  id: string
  scenario_id: string
  agent_artifact_id: string
  agent_version: number
  dataset_id: string
  dataset_version: number
  mode: string
  tier: string
  trials: number
  rows: number
  status: 'queued' | 'running' | 'paused' | 'cancelled' | 'completed' | 'stopped_budget'
  budget_usd: number | null
  spent_usd: number
  estimate: BatchEstimate & { trigger?: string }
  load_settings: Record<string, number>
  progress: { total: number; finished: number; passed: number; failed: number; errored: number; cancelled: number }
  created_at: string
  finished_at: string | null
}

export interface BatchSchedule {
  id: string
  scenario_id: string
  tier: BatchTier
  trials: number
  every_minutes: number | null
  on_dataset_change: boolean
  on_context_change: boolean
  budget_usd: number | null
  enabled: boolean
  last_run_at: string | null
}

// Epic 35 -- results at volume
export interface Rate {
  passed: number
  total: number
  rate: number | null
  low: number | null
  high: number | null
}

export interface SegmentResult {
  segment: string
  rate: Rate
  weak: boolean
}

export interface FailureCluster {
  pattern: string
  count: number
  example_reason: string
  row_keys: string[]
  run_ids: string[]
}

export interface Distribution {
  median: number | null
  p95: number | null
  max: number | null
}

export interface BatchResults {
  batch_id: string
  mode: string
  status: string
  headline: Rate
  reading: string
  excluded: Record<string, number>
  segments: SegmentResult[]
  provenance: SegmentResult[]
  decision_matrix: Record<string, Record<string, number>>
  field_accuracy: Record<string, Rate>
  clusters: FailureCluster[]
  consistency: {
    trials: number
    always_pass: number
    always_fail: number
    inconsistent: number
    pass_at_k: number | null
    pass_hat_k: number | null
    inconsistent_rows: string[]
  }
  cost_usd: Distribution
  tokens: Distribution
  seconds: Distribution
  worst_rows: { run_id: string; row_key: string | null; cost_usd: number | null; seconds: number | null; passed: boolean }[]
  relations: { relation_id: string; name: string; kind: string; checked: number; held: number; broken_rows: string[] }[]
  load: Record<string, number | null> | null
  critical_failures: string[]
}

export interface BatchComparison {
  batch_a: string
  batch_b: string
  agent_versions: [number, number]
  paired_rows: number
  fixed: string[]
  regressed: string[]
  both_pass: number
  both_fail: number
  rate_a: Rate
  rate_b: Rate
  p_value: number
  significant: boolean
  reading: string
}

export interface ReleaseCriteria {
  min_pass_rate_lower: number
  min_segment_rate: number | null
  critical_must_pass: boolean
  no_significant_regression: boolean
  tiers: string[]
}

export interface GateResult {
  passed: boolean
  batch_id: string | null
  reasons: string[]
  checked_at: string
}

// ---- Epic 36: process digital twin ----------------------------------------

export interface Triangular {
  min: number
  likely: number
  max: number
}

export interface RoleCost {
  role: string
  hourly_cost: number
}

export interface NodeOperatingModel {
  node_id: string
  label: string
  role: string
  handling_minutes: Triangular | null
  wait_minutes: Triangular | null
  hourly_cost: number | null
  review_minutes: Triangular | null
  to_be_wait_minutes: Triangular | null
}

export interface OperatingModel {
  currency: string
  monthly_volume: number
  productive_hours_per_fte: number
  working_minutes_per_day: number
  roles: RoleCost[]
  nodes: NodeOperatingModel[]
  end_event_outcomes: Record<string, string>
  outcome_label: string
  usd_to_currency: number
  default_review_minutes: Triangular
}

export interface OperatingModelView extends OperatingModel {
  process_id: string
  version: number
  is_default: boolean
  updated_at: string | null
}

export interface ProcessTwinRequest {
  scenario_id: string
  dataset_id: string
  name?: string
  sample_size?: number | null
  budget_usd?: number | null
  seed?: number
}

export interface ProcessTwinEstimate {
  cases: number
  cost_per_case_usd: number
  total_cost_usd: number
  basis: string
}

export interface ProcessTwinBatch {
  id: string
  process_id: string
  name: string
  scenario_id: string
  dataset_id: string
  dataset_version: number
  status: string
  budget_usd: number | null
  seed: number
  cases: number
  finished: number
  spent_usd: number
  operating_model_version: number
  created_at: string
  finished_at: string | null
}

export interface Interval {
  mean: number | null
  low: number | null
  high: number | null
}

export interface CaseEfficiency {
  run_id: string
  row_key: string | null
  segment: string
  status: string
  outcome: string | null
  expected_outcome: string | null
  correct: boolean | null
  path: string[]
  straight_through: boolean
  human_touches: number
  as_is_cycle_minutes: number
  to_be_cycle_minutes: number
  as_is_touch_minutes: number
  to_be_touch_minutes: number
  as_is_cost: number
  to_be_labour_cost: number
  llm_cost_usd: number
  agent_seconds: number
  failure: string | null
}

export interface NodeEfficiency {
  node_id: string
  label: string
  kind: string
  visits: number
  failures: number
  mean_agent_seconds: number | null
  review_rate: number | null
  mean_llm_cost_usd: number | null
  as_is_touch_minutes: number
  to_be_touch_minutes: number
}

export interface EfficiencyReport {
  cases: number
  completed: number
  failed: number
  currency: string
  straight_through: Rate
  accuracy: Rate
  as_is_cycle_minutes: Interval
  to_be_cycle_minutes: Interval
  cycle_time_reduction: Interval
  as_is_touch_minutes: Interval
  to_be_touch_minutes: Interval
  touch_time_reduction: Interval
  as_is_cost_per_case: Interval
  to_be_cost_per_case: Interval
  llm_cost_per_case_usd: Interval
  annual_hours_released: Interval
  annual_fte_released: Interval
  annual_cost_saved: Interval
  assumptions: string[]
  headline: string
  nodes: NodeEfficiency[]
  segments: { segment: string; cases: number; accuracy: Rate; straight_through: number; mean_touch_saved_minutes: number }[]
  outcome_matrix: { expected: string; actual: string; count: number }[]
  path_variants: { path: string[]; cases: number }[]
  cases_detail: CaseEfficiency[]
  usd_to_currency: number
}
