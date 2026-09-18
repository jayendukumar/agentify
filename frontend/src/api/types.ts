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
  created_at: string
}

export interface DocumentDetail extends DocumentSummary {
  error_message: string | null
}

export interface BPMNDocument {
  process_id: string
  xml: string
  low_confidence_element_ids: string[]
  validation_issues: string[]
  generated_at: string
}

export interface BlueprintOverlay {
  process_id: string
  baseline_version_id: string
  nodes: unknown[]
  generated_at: string
}

export interface ApiErrorBody {
  detail: string
}
