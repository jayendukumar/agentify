import { useState } from 'react'
import { ApiError, generateReviewerPersonas, generateTestData } from '../api/client'
import type { DataMode, GeneratedRecord, ReviewerPersona, TestBuilderContext } from '../api/types'
import { formatValue, REVIEWER_LABELS } from '../lib/twinSituations'

// Epic 25: the Test Data Kitchen -- sample packs, describe-it generation,
// "make it tricky" mutations, clone & disguise, and reviewer casting. All
// generated items land on a shelf the user assigns to situations; the
// shelf is saved to the agent's test kit (a Twin Environment's
// datasets/personas) by the parent.

const PACKS: { mode: DataMode; label: string; hint: string }[] = [
  { mode: 'typical', label: 'Typical cases', hint: 'Everyday, realistic records' },
  { mode: 'edge', label: 'Edge cases', hint: 'Valid but unusual -- limits, rare values' },
  { mode: 'messy', label: 'Messy real-world', hint: 'Typos, gaps, inconsistent formats' },
]

function promptChips(context: TestBuilderContext): string[] {
  const first = context.input_fields[0]?.name
  const numeric = context.input_fields.find((f) => /number|amount|int|decimal|currency/i.test(f.format))?.name
  return [
    'Records from customers in different countries and languages',
    first ? `A few records where ${first} is missing or blank` : 'A few records with missing details',
    numeric ? `Very high and very low values for ${numeric}` : 'Records at the boundaries of what is allowed',
    'Two near-duplicates of the same case submitted twice',
  ]
}

export default function TestDataKitchen({
  processId,
  context,
  shelf,
  onShelfChange,
  personas,
  onPersonasChange,
  situationTitles,
  onUseRecord,
  onSaveKit,
  savingKit,
  kitSaved,
  canEdit,
}: {
  processId: string
  context: TestBuilderContext
  shelf: GeneratedRecord[]
  onShelfChange: (next: GeneratedRecord[]) => void
  personas: ReviewerPersona[]
  onPersonasChange: (next: ReviewerPersona[]) => void
  situationTitles: string[]
  onUseRecord: (situationIndex: number, record: Record<string, unknown>) => void
  onSaveKit: () => void
  savingKit: boolean
  kitSaved: boolean
  canEdit: boolean
}) {
  const [busy, setBusy] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [prompt, setPrompt] = useState('')
  const [sample, setSample] = useState('')
  const [personaPrompt, setPersonaPrompt] = useState('')

  async function generate(
    key: string,
    body: Parameters<typeof generateTestData>[2],
    { replaceIndex }: { replaceIndex?: number } = {},
  ) {
    setBusy(key)
    setError(null)
    try {
      const records = await generateTestData(processId, context.artifact_id, body)
      if (replaceIndex !== undefined) {
        onShelfChange([...shelf.slice(0, replaceIndex + 1), ...records, ...shelf.slice(replaceIndex + 1)])
      } else {
        onShelfChange([...records, ...shelf])
      }
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not generate data -- please try again')
    } finally {
      setBusy(null)
    }
  }

  async function castReviewers() {
    setBusy('personas')
    setError(null)
    try {
      onPersonasChange(
        await generateReviewerPersonas(processId, context.artifact_id, { count: 3, prompt: personaPrompt || null }),
      )
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not cast reviewers -- please try again')
    } finally {
      setBusy(null)
    }
  }

  const disabled = !canEdit || busy !== null

  return (
    <aside className="data-kitchen" aria-label="Test data kitchen" data-testid="data-kitchen">
      <h4>Test data kitchen</h4>
      <p className="meta">
        Cook up realistic, fully synthetic data for your situations. Generated with AI from this agent's input
        fields ({context.input_fields.map((f) => f.name).join(', ') || 'none declared'}).
      </p>

      <div className="kitchen-packs">
        {PACKS.map((pack) => (
          <button
            key={pack.mode}
            type="button"
            className="kitchen-pack"
            disabled={disabled}
            title={pack.hint}
            onClick={() => generate(pack.mode, { mode: pack.mode, count: 4 })}
          >
            <span className="kitchen-pack-label">{busy === pack.mode ? 'Cooking…' : pack.label}</span>
            <span className="meta">{pack.hint}</span>
          </button>
        ))}
      </div>

      <label className="kitchen-describe">
        Describe the data you need
        <textarea
          rows={2}
          value={prompt}
          placeholder="e.g. 10 EU suppliers, 20% missing VAT IDs, two duplicates"
          onChange={(e) => setPrompt(e.target.value)}
        />
      </label>
      <div className="chip-row">
        {promptChips(context).map((chip) => (
          <button key={chip} type="button" className="button-secondary chip-button" onClick={() => setPrompt(chip)}>
            {chip}
          </button>
        ))}
      </div>
      <button
        type="button"
        className="button-primary"
        disabled={disabled || prompt.trim() === ''}
        onClick={() => generate('describe', { mode: 'describe', prompt, count: 5 })}
      >
        {busy === 'describe' ? 'Generating…' : '✨ Generate'}
      </button>

      <details className="kitchen-clone">
        <summary>Clone &amp; disguise a real example</summary>
        <p className="meta">
          Paste one real record (text, CSV, or JSON). AI produces look-alike records with every name, number and
          address replaced. The pasted example is sent to the configured AI provider but never stored.
        </p>
        <textarea rows={4} value={sample} onChange={(e) => setSample(e.target.value)} aria-label="Example to clone" />
        <button
          type="button"
          disabled={disabled || sample.trim() === ''}
          onClick={() => generate('clone', { mode: 'clone', sample_text: sample, count: 5 })}
        >
          {busy === 'clone' ? 'Disguising…' : 'Clone & disguise'}
        </button>
      </details>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <h5>
        On the shelf <span className="meta">({shelf.length})</span>
      </h5>
      {shelf.length === 0 ? (
        <p className="meta">Nothing yet -- pick a pack or describe what you need.</p>
      ) : (
        <ul className="shelf-list">
          {shelf.map((item, index) => (
            <li key={`${item.label}-${index}`} className="shelf-record" data-testid="shelf-record">
              <div className="shelf-record-header">
                <strong>{item.label}</strong>
                <button
                  type="button"
                  className="link-button"
                  aria-label={`Remove ${item.label}`}
                  onClick={() => onShelfChange(shelf.filter((_, i) => i !== index))}
                >
                  ×
                </button>
              </div>
              {item.note && <p className="meta">{item.note}</p>}
              <dl className="record-fields">
                {Object.entries(item.record).map(([k, v]) => (
                  <div key={k}>
                    <dt>{k}</dt>
                    <dd>{formatValue(v)}</dd>
                  </div>
                ))}
              </dl>
              <div className="shelf-record-actions">
                {situationTitles.length > 0 && (
                  <select
                    aria-label={`Use ${item.label} in a situation`}
                    value=""
                    onChange={(e) => {
                      if (e.target.value !== '') onUseRecord(Number(e.target.value), item.record)
                    }}
                  >
                    <option value="">Use in…</option>
                    {situationTitles.map((title, i) => (
                      <option key={i} value={i}>
                        {title}
                      </option>
                    ))}
                  </select>
                )}
                <button
                  type="button"
                  className="button-secondary"
                  disabled={disabled}
                  onClick={() =>
                    generate(`mutate-${index}`, { mode: 'mutate', seed_record: item.record, count: 4 }, { replaceIndex: index })
                  }
                >
                  {busy === `mutate-${index}` ? 'Twisting…' : 'Make it tricky'}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      {context.human_checkpoint !== 'none' && (
        <>
          <h5>Cast the reviewers</h5>
          <p className="meta">Generate reviewer characters, then cast them into a situation in its "The reviewer" section.</p>
          <input
            value={personaPrompt}
            placeholder="Optional: e.g. a strict finance manager and a hurried team lead"
            onChange={(e) => setPersonaPrompt(e.target.value)}
            aria-label="Describe the reviewers"
          />
          <button type="button" className="button-secondary" disabled={disabled} onClick={castReviewers}>
            {busy === 'personas' ? 'Casting…' : '🎭 Cast reviewers'}
          </button>
          {personas.length > 0 && (
            <ul className="persona-list">
              {personas.map((p) => (
                <li key={p.name} className="persona-card">
                  <strong>
                    {p.name}, {p.role}
                  </strong>
                  <span className="meta">{p.description}</span>
                  <span className="badge">
                    {p.reviewer.behaviour === 'threshold'
                      ? `Rejects when ${p.reviewer.field} > ${p.reviewer.threshold}`
                      : REVIEWER_LABELS[p.reviewer.behaviour]}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {(shelf.length > 0 || personas.length > 0) && (
        <button type="button" className="button-secondary kitchen-save" disabled={!canEdit || savingKit} onClick={onSaveKit}>
          {savingKit ? 'Saving…' : kitSaved ? '✓ Saved to test kit' : 'Save shelf to test kit'}
        </button>
      )}
    </aside>
  )
}
