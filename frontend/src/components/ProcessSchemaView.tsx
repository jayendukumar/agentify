import type { ProcessSchema } from '../api/types'

export default function ProcessSchemaView({ schema }: { schema: ProcessSchema }) {
  const actorById = new Map(schema.actors.map((a) => [a.id, a]))
  const elementById = new Map(schema.elements.map((e) => [e.id, e]))

  if (schema.elements.length === 0) {
    return (
      <div className="schema-view">
        <h3>Extracted Process</h3>
        <p>No process steps were identified in the uploaded document(s) yet.</p>
      </div>
    )
  }

  return (
    <div className="schema-view">
      <h3>Extracted Process</h3>

      {schema.actors.length > 0 && (
        <div className="schema-section">
          <h4>Actors ({schema.actors.length})</h4>
          <ul className="actor-list">
            {schema.actors.map((actor) => (
              <li key={actor.id}>
                <span>{actor.name}</span>
                <span className="badge">{actor.type.replace('_', ' ')}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="schema-section">
        <h4>Steps ({schema.elements.length})</h4>
        <ol className="element-list">
          {schema.elements.map((element) => {
            const actor = element.actor_id ? actorById.get(element.actor_id) : undefined
            return (
              <li key={element.id} className="element-card">
                <div className="element-header">
                  <span className="badge">{element.type.replace('_', ' ')}</span>
                  <span className="element-label">{element.label}</span>
                  <span className={`badge confidence-${element.confidence}`}>{element.confidence} confidence</span>
                </div>
                {actor && <div className="element-meta">Actor: {actor.name}</div>}
                {element.inputs.length > 0 && <div className="element-meta">Inputs: {element.inputs.join(', ')}</div>}
                {element.outputs.length > 0 && (
                  <div className="element-meta">Outputs: {element.outputs.join(', ')}</div>
                )}
                {element.systems_touched.length > 0 && (
                  <div className="element-meta">Systems: {element.systems_touched.join(', ')}</div>
                )}
                {element.source_refs.length > 0 && (
                  <details className="element-sources">
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
              </li>
            )
          })}
        </ol>
      </div>

      {schema.flows.length > 0 && (
        <div className="schema-section">
          <h4>Flows ({schema.flows.length})</h4>
          <ul className="flow-list">
            {schema.flows.map((flow) => {
              const fromLabel = elementById.get(flow.from)?.label ?? flow.from
              const toLabel = elementById.get(flow.to)?.label ?? flow.to
              return (
                <li key={flow.id}>
                  {fromLabel} &rarr; {toLabel}
                  {flow.condition && <span className="meta"> ({flow.condition})</span>}
                </li>
              )
            })}
          </ul>
        </div>
      )}
    </div>
  )
}
