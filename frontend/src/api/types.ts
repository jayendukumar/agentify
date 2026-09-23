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
}

export interface AgentArtifact {
  id: string
  process_id: string
  group_key: string
  node_ids: string[]
  primary_node_id: string
  status: AgentArtifactStatus
  definition: AgentDefinition
  baseline_version_id: string
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

// Epic 14 (core slice)
export type TwinSystemStubMode = 'proxy' | 'static'
export type TwinHumanCheckpointMode = 'probability' | 'rule'
export type TwinRuleOperator = 'eq' | 'ne' | 'gt' | 'gte' | 'lt' | 'lte'
export type TwinTraceStepKind = 'tool_call' | 'human_checkpoint'
export type TwinRunStatus = 'passed' | 'failed' | 'error'

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

export interface TwinExpectedStep {
  kind: TwinTraceStepKind
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
  created_at: string
  created_by: string | null
  created_by_name: string | null
}

export interface TwinTraceStep {
  kind: TwinTraceStepKind
  target: string
  arguments: Record<string, unknown>
  result: Record<string, unknown> | null
  decision: 'approve' | 'reject' | null
  static_fallback: boolean
}

export interface TwinDeviation {
  reason: string
  step_index: number | null
}

export interface TwinRun {
  id: string
  scenario_id: string
  agent_artifact_id: string
  status: TwinRunStatus
  trace: TwinTraceStep[]
  final_output: Record<string, unknown> | null
  deviations: TwinDeviation[]
  total_cost_usd: number | null
  total_tokens: number
  turns_used: number
  started_at: string
  completed_at: string
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
