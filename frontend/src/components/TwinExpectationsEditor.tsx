import type { Situation, SituationExpectation, TestBuilderContext } from '../api/types'
import { describeExpectedPath } from '../lib/twinSituations'

// Epic 24, US24.4: step 4 of the guided builder -- "what should happen"
// as checkboxes pre-filled from the blueprint, with the compiled expected
// path read back in plain words so the grading criteria are visible.
export default function TwinExpectationsEditor({
  situation,
  context,
  onChange,
}: {
  situation: Situation
  context: TestBuilderContext
  onChange: (next: Situation) => void
}) {
  const { story } = situation
  const expect = story.expect

  function setExpect(patch: Partial<SituationExpectation>) {
    onChange({ ...situation, story: { ...story, expect: { ...expect, ...patch } } })
  }

  function toggleSystem(system: string, checked: boolean) {
    // Kept in the agent's own system order so the expected path is stable.
    const next = new Set(expect.systems_used)
    if (checked) next.add(system)
    else next.delete(system)
    setExpect({ systems_used: context.systems.filter((s) => next.has(s)) })
  }

  function toggleOutput(field: string, checked: boolean) {
    const next = new Set(expect.output_fields)
    if (checked) next.add(field)
    else next.delete(field)
    setExpect({ output_fields: context.output_fields.map((f) => f.name).filter((f) => next.has(f)) })
  }

  return (
    <section className="expectations-editor" aria-label={`What should happen: ${story.title}`} data-testid="expectations-editor">
      <h4>{story.title}</h4>
      {story.then && <p className="meta">Story says: {story.then}</p>}

      {context.systems.length > 0 && (
        <fieldset>
          <legend>The agent should use</legend>
          {context.systems.map((system) => (
            <label key={system} className="check-row">
              <input
                type="checkbox"
                checked={expect.systems_used.includes(system)}
                onChange={(e) => toggleSystem(system, e.target.checked)}
              />
              {system}
              {story.system_behaviour[system] && story.system_behaviour[system] !== 'normal' && (
                <span className="meta"> (set up to misbehave in this situation)</span>
              )}
            </label>
          ))}
        </fieldset>
      )}

      {context.human_checkpoint !== 'none' && (
        <fieldset>
          <legend>A reviewer should</legend>
          {(
            [
              ['approve', 'Be asked, and approve'],
              ['reject', 'Be asked, and reject'],
              [null, "Doesn't matter / may not be asked"],
            ] as const
          ).map(([value, label]) => (
            <label key={String(value)} className="check-row">
              <input
                type="radio"
                name={`review-${story.title}`}
                checked={expect.review_decision === value}
                onChange={() => setExpect({ review_decision: value })}
              />
              {label}
            </label>
          ))}
        </fieldset>
      )}

      {context.output_fields.length > 0 && (
        <fieldset>
          <legend>The result must include</legend>
          {context.output_fields.map((field) => (
            <label key={field.name} className="check-row">
              <input
                type="checkbox"
                checked={expect.output_fields.includes(field.name)}
                onChange={(e) => toggleOutput(field.name, e.target.checked)}
              />
              {field.name} <span className="meta">→ {field.source_or_destination}</span>
            </label>
          ))}
        </fieldset>
      )}

      <p className="expected-path" data-testid="expected-path">
        <span className="meta">Expected path:</span> {describeExpectedPath(story, context.human_checkpoint)}
      </p>
    </section>
  )
}
