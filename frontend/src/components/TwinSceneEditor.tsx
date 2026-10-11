import { useId, useState } from 'react'
import type { ReviewerPersona, ReviewerSetup, Situation, SystemBehaviour, TestBuilderContext } from '../api/types'
import { formatValue, parseFieldValue, REVIEWER_LABELS, SYSTEM_BEHAVIOUR_LABELS } from '../lib/twinSituations'

// Epic 24, US24.3 + Epic 25, US25.6: step 3 of the guided builder for one
// situation -- the case's input data as a readable form, each system's
// behaviour and the reviewer's as plain choices. No JSON anywhere: a
// system's "specific data" is entered as `field: value` lines.

function toLines(data: Record<string, unknown> | undefined): string {
  return Object.entries(data ?? {})
    .map(([k, v]) => `${k}: ${formatValue(v)}`)
    .join('\n')
}

function fromLines(text: string): Record<string, unknown> {
  const result: Record<string, unknown> = {}
  for (const line of text.split('\n')) {
    const i = line.indexOf(':')
    if (i <= 0) continue
    const key = line.slice(0, i).trim()
    const raw = line.slice(i + 1).trim()
    const n = Number(raw)
    result[key] = raw !== '' && !Number.isNaN(n) ? n : raw
  }
  return result
}

export default function TwinSceneEditor({
  situation,
  context,
  personas,
  onChange,
  onRemove,
}: {
  situation: Situation
  context: TestBuilderContext
  personas: ReviewerPersona[]
  onChange: (next: Situation) => void
  onRemove: () => void
}) {
  const id = useId()
  const { story } = situation
  const [dataDrafts, setDataDrafts] = useState<Record<string, string>>(() =>
    Object.fromEntries(Object.entries(story.system_data).map(([k, v]) => [k, toLines(v)])),
  )
  const fieldNames = Array.from(new Set([...context.input_fields.map((f) => f.name), ...Object.keys(situation.inputs)]))
  const formatOf = (name: string) => context.input_fields.find((f) => f.name === name)?.format

  function setStory(patch: Partial<Situation['story']>) {
    onChange({ ...situation, story: { ...story, ...patch } })
  }

  function setReviewer(patch: Partial<ReviewerSetup>) {
    setStory({ reviewer: { ...story.reviewer, ...patch } })
  }

  function setBehaviour(system: string, behaviour: SystemBehaviour) {
    const systemData = { ...story.system_data }
    if (behaviour !== 'custom_data') delete systemData[system]
    setStory({ system_behaviour: { ...story.system_behaviour, [system]: behaviour }, system_data: systemData })
  }

  return (
    <section className="scene-editor" aria-label={`Set the scene: ${story.title}`} data-testid="scene-editor">
      <div className="scene-editor-header">
        <label className="scene-title">
          <span className="sr-only">Situation name</span>
          <input value={story.title} onChange={(e) => setStory({ title: e.target.value })} />
        </label>
        <button type="button" className="button-secondary" onClick={onRemove}>
          Remove
        </button>
      </div>

      <details className="scene-story-edit">
        <summary>Edit the story</summary>
        {(['given', 'when', 'then'] as const).map((part) => (
          <label key={part}>
            {part[0].toUpperCase() + part.slice(1)}
            <textarea rows={2} value={story[part]} onChange={(e) => setStory({ [part]: e.target.value })} />
          </label>
        ))}
      </details>

      <h5>The case</h5>
      {fieldNames.length === 0 ? (
        <p className="meta">This agent doesn't declare any input fields.</p>
      ) : (
        <div className="record-form">
          {fieldNames.map((name) => (
            <label key={name}>
              <span>
                {name}
                {formatOf(name) && <span className="meta"> ({formatOf(name)})</span>}
              </span>
              <input
                value={formatValue(situation.inputs[name]) === '—' ? '' : formatValue(situation.inputs[name])}
                onChange={(e) =>
                  onChange({
                    ...situation,
                    inputs: { ...situation.inputs, [name]: parseFieldValue(e.target.value, situation.inputs[name]) },
                  })
                }
              />
            </label>
          ))}
        </div>
      )}

      {context.systems.length > 0 && (
        <>
          <h5>How the systems behave</h5>
          <div className="system-behaviour-list">
            {context.systems.map((system) => {
              const behaviour = story.system_behaviour[system] ?? 'normal'
              return (
                <div key={system} className="system-behaviour-row">
                  <label>
                    <span>{system}</span>
                    <select value={behaviour} onChange={(e) => setBehaviour(system, e.target.value as SystemBehaviour)}>
                      {Object.entries(SYSTEM_BEHAVIOUR_LABELS).map(([value, label]) => (
                        <option key={value} value={value}>
                          {label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {behaviour === 'custom_data' && (
                    <label className="system-data">
                      What should {system} return? One <code>field: value</code> per line.
                      <textarea
                        rows={3}
                        value={dataDrafts[system] ?? ''}
                        placeholder={'status: active\ncredit_limit: 5000'}
                        onChange={(e) => {
                          setDataDrafts((prev) => ({ ...prev, [system]: e.target.value }))
                          setStory({ system_data: { ...story.system_data, [system]: fromLines(e.target.value) } })
                        }}
                      />
                    </label>
                  )}
                </div>
              )
            })}
          </div>
        </>
      )}

      {context.human_checkpoint !== 'none' && (
        <>
          <h5>The reviewer</h5>
          <div className="reviewer-row">
            <label>
              <span>{story.reviewer.persona_name ?? 'Reviewer'}</span>
              <select
                value={story.reviewer.behaviour}
                onChange={(e) =>
                  setReviewer({ behaviour: e.target.value as ReviewerSetup['behaviour'], persona_name: null })
                }
              >
                {(['approve', 'reject', 'threshold'] as const).map((value) => (
                  <option key={value} value={value}>
                    {REVIEWER_LABELS[value]}
                  </option>
                ))}
              </select>
            </label>
            {story.reviewer.behaviour === 'threshold' && (
              <>
                <label>
                  <span>Field to check</span>
                  <input
                    list={`${id}-fields`}
                    value={story.reviewer.field ?? ''}
                    onChange={(e) => setReviewer({ field: e.target.value || null })}
                  />
                  <datalist id={`${id}-fields`}>
                    {fieldNames.map((name) => (
                      <option key={name} value={name} />
                    ))}
                  </datalist>
                </label>
                <label>
                  <span>Rejects above</span>
                  <input
                    type="number"
                    value={story.reviewer.threshold ?? ''}
                    onChange={(e) => setReviewer({ threshold: e.target.value === '' ? null : Number(e.target.value) })}
                  />
                </label>
              </>
            )}
          </div>
          {personas.length > 0 && (
            <p className="persona-cast">
              <span className="meta">Cast a reviewer:</span>{' '}
              {personas.map((persona) => (
                <button
                  key={persona.name}
                  type="button"
                  className="button-secondary chip-button"
                  title={persona.description}
                  onClick={() => setStory({ reviewer: { ...persona.reviewer, persona_name: persona.name } })}
                >
                  {persona.name}, {persona.role}
                </button>
              ))}
            </p>
          )}
        </>
      )}
    </section>
  )
}
