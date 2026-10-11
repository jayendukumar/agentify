export interface ProcessSummary {
  id: string
  name: string
  document_count: number
  has_draft_bpmn: boolean
  finalized_version_count: number
  created_at: string
  updated_at: string
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
}

export interface BlueprintOverlay {
  process_id: string
  baseline_version_id: string
  nodes: BlueprintNodeResult[]
  generated_at: string
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

export type GapKind = 'structural' | 'cross_document'
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
export type TwinSystemStubMode = 'proxy' | 'static'
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
}

// Epics 24/25: the guided test builder -- see backend/app/schemas/
// twin_builder.py and twin.py's ScenarioStory.
export type CoverageKind = 'happy_path' | 'gateway_branch' | 'checkpoint' | 'system_failure' | 'data_quality' | 'custom'
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

export interface SituationExpectation {
  systems_used: string[]
  review_decision: 'approve' | 'reject' | null
  output_fields: string[]
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
}

export type DataMode = 'typical' | 'edge' | 'messy' | 'describe' | 'mutate' | 'clone'

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
export type DataMappingMode = 'llm_adapter' | 'exact_field_contract'

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

export type Role = 'viewer' | 'editor'

export interface User {
  id: string
  name: string
  role: Role
  created_at: string
}

export interface ApiErrorBody {
  detail: string
}
