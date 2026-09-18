import type { ProcessSchema } from '../api/types'

// Mirrors the id conventions in backend/app/bpmn/mapping.py: node ids are
// "<Prefix>_<schema element id>", flow ids are "Flow_<schema flow id>", lane
// ids are "Lane_<schema actor id>". No metadata is embedded in the BPMN XML
// itself (see backend/app/bpmn/builder.py), so this join is the only way to
// recover it client-side.
const ELEMENT_PREFIXES = ['Task_', 'Gateway_', 'Event_']

function classifyBpmnId(id: string): { kind: 'element' | 'flow' | 'lane' | 'unknown'; schemaId: string } {
  for (const prefix of ELEMENT_PREFIXES) {
    if (id.startsWith(prefix)) return { kind: 'element', schemaId: id.slice(prefix.length) }
  }
  if (id.startsWith('Flow_')) return { kind: 'flow', schemaId: id.slice('Flow_'.length) }
  if (id.startsWith('Lane_')) return { kind: 'lane', schemaId: id.slice('Lane_'.length) }
  return { kind: 'unknown', schemaId: id }
}

export default function ElementDetailPanel({
  elementId,
  schema,
}: {
  elementId: string | null
  schema: ProcessSchema | null
}) {
  if (!elementId) {
    return (
      <aside className="element-detail-panel">
        <p className="meta">Select a node or connection to see its details.</p>
      </aside>
    )
  }

  if (!schema) {
    return (
      <aside className="element-detail-panel">
        <p className="meta">No extracted process data available.</p>
      </aside>
    )
  }

  const { kind, schemaId } = classifyBpmnId(elementId)
  const actorById = new Map(schema.actors.map((a) => [a.id, a]))
  const elementById = new Map(schema.elements.map((e) => [e.id, e]))

  if (kind === 'element') {
    const element = elementById.get(schemaId)
    if (!element) {
      return (
        <aside className="element-detail-panel">
          <p className="meta">Manually added &mdash; no extracted metadata for this element.</p>
        </aside>
      )
    }
    const actor = element.actor_id ? actorById.get(element.actor_id) : undefined
    return (
      <aside className="element-detail-panel">
        <div className="element-card">
          <div className="element-header">
            <span className="badge">{element.type.replace('_', ' ')}</span>
            <span className="element-label">{element.label}</span>
            <span className={`badge confidence-${element.confidence}`}>{element.confidence} confidence</span>
          </div>
          {actor && <div className="element-meta">Actor: {actor.name}</div>}
          {element.inputs.length > 0 && <div className="element-meta">Inputs: {element.inputs.join(', ')}</div>}
          {element.outputs.length > 0 && <div className="element-meta">Outputs: {element.outputs.join(', ')}</div>}
          {element.systems_touched.length > 0 && (
            <div className="element-meta">Systems: {element.systems_touched.join(', ')}</div>
          )}
          {element.source_refs.length > 0 && (
            <details className="element-sources" open>
              <summary>
                {element.source_refs.length} source reference{element.source_refs.length === 1 ? '' : 's'}
              </summary>
              <ul>
                {element.source_refs.map((ref, index) => (
                  <li key={index}>
                    <span className="meta">{ref.location}:</span> &quot;{ref.excerpt}&quot;
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      </aside>
    )
  }

  if (kind === 'flow') {
    const flow = schema.flows.find((f) => f.id === schemaId)
    if (!flow) {
      return (
        <aside className="element-detail-panel">
          <p className="meta">Manually added &mdash; no extracted metadata for this connection.</p>
        </aside>
      )
    }
    const fromLabel = elementById.get(flow.from)?.label ?? flow.from
    const toLabel = elementById.get(flow.to)?.label ?? flow.to
    return (
      <aside className="element-detail-panel">
        <div className="element-card">
          <div className="element-header">
            <span className="badge">flow</span>
            <span className="element-label">
              {fromLabel} &rarr; {toLabel}
            </span>
          </div>
          {flow.condition && <div className="element-meta">Condition: {flow.condition}</div>}
        </div>
      </aside>
    )
  }

  if (kind === 'lane') {
    const actor = actorById.get(schemaId)
    if (!actor) {
      return (
        <aside className="element-detail-panel">
          <p className="meta">No extracted metadata for this lane.</p>
        </aside>
      )
    }
    return (
      <aside className="element-detail-panel">
        <div className="element-card">
          <div className="element-header">
            <span className="badge">actor</span>
            <span className="element-label">{actor.name}</span>
            <span className="badge">{actor.type.replace('_', ' ')}</span>
          </div>
        </div>
      </aside>
    )
  }

  return (
    <aside className="element-detail-panel">
      <p className="meta">No extracted metadata for this element.</p>
    </aside>
  )
}
