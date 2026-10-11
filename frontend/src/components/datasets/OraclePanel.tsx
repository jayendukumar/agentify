import { useEffect, useState } from 'react'
import {
  ApiError,
  calibrateJudge,
  createJudgeRubric,
  createRelation,
  generateLabelFirst,
  generateVariants,
  getReviewQueue,
  labelWithOracle,
  listJudgeRubrics,
  listOracleConflicts,
  listRelations,
  reportRulesGap,
} from '../../api/client'
import type { JudgeRubric, MetamorphicRelation, OracleConflict, ReviewItem } from '../../api/types'

interface Props {
  processId: string
  datasetId: string
  canEdit: boolean
  onChanged: () => void
}

/** Epic 33: labels from the rules, conflicts, label-first generation,
 * metamorphic variants, the review queue, and calibrated judges -- for one
 * dataset. */
export default function OraclePanel({ processId, datasetId, canEdit, onChanged }: Props) {
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [conflicts, setConflicts] = useState<OracleConflict[]>([])
  const [queue, setQueue] = useState<ReviewItem[]>([])
  const [relations, setRelations] = useState<MetamorphicRelation[]>([])
  const [rubrics, setRubrics] = useState<JudgeRubric[]>([])
  const [busy, setBusy] = useState<string | null>(null)

  const [total, setTotal] = useState('100')
  const [approve, setApprove] = useState('70')
  const [reject, setReject] = useState('20')
  const [escalate, setEscalate] = useState('10')
  const [messy, setMessy] = useState('15')

  const [relName, setRelName] = useState('')
  const [relKind, setRelKind] = useState<'invariance' | 'directional'>('invariance')
  const [relField, setRelField] = useState('')
  const [relType, setRelType] = useState('uppercase')
  const [relAmount, setRelAmount] = useState('')
  const [relDirection, setRelDirection] = useState('toward_escalate')

  const [rubricName, setRubricName] = useState('')
  const [rubricField, setRubricField] = useState('')
  const [rubricText, setRubricText] = useState('')

  async function load() {
    try {
      const [c, q, r, j] = await Promise.all([
        listOracleConflicts(processId, datasetId),
        getReviewQueue(processId, datasetId),
        listRelations(processId, datasetId),
        listJudgeRubrics(processId),
      ])
      setConflicts(c)
      setQueue(q)
      setRelations(r)
      setRubrics(j)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load' })
    }
  }

  useEffect(() => {
    void load()
  }, [processId, datasetId])

  async function run(key: string, action: () => Promise<string>) {
    setBusy(key)
    setMessage(null)
    try {
      setMessage({ kind: 'info', text: await action() })
      await load()
      onChanged()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'That did not work' })
    } finally {
      setBusy(null)
    }
  }

  const disabled = !canEdit || busy !== null

  return (
    <section aria-labelledby="oracle-heading" className="oracle-panel">
      <h2 id="oracle-heading">Answers at scale</h2>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}

      <div className="registry-entry-actions">
        <button
          type="button"
          className="button-secondary"
          disabled={disabled}
          onClick={() => run('label', async () => {
            const r = await labelWithOracle(processId, datasetId)
            return `Rules labelled ${r.labelled} cases; ${r.agreed} already agreed; ${r.conflicts} disagree; ${r.undetermined} not decided by the rules.`
          })}
        >
          {busy === 'label' ? 'Labelling...' : 'Label unlabelled cases with the rules'}
        </button>
      </div>

      {conflicts.length > 0 && (
        <>
          <h3>Rules and labels disagree ({conflicts.length})</h3>
          <p className="meta">Either the label is wrong, or the documented rule doesn't match how the process really runs.</p>
          <ul>
            {conflicts.slice(0, 20).map((c) => (
              <li key={c.row_key}>
                {Object.entries(c.inputs).slice(0, 3).map(([k, v]) => `${k}: ${String(v)}`).join(', ')} -- labelled <strong>{c.label}</strong>, rules say <strong>{c.oracle}</strong>
              </li>
            ))}
          </ul>
          {canEdit && (
            <button
              type="button"
              className="button-secondary"
              disabled={disabled}
              onClick={() => run('gap', async () => {
                await reportRulesGap(processId, datasetId, conflicts.map((c) => c.row_key))
                return 'Raised as a gap on the process for the process owner to resolve.'
              })}
            >
              The rules are out of date -- raise a process gap
            </button>
          )}
        </>
      )}

      {canEdit && (
        <form
          className="create-form"
          onSubmit={(e) => {
            e.preventDefault()
            void run('generate', async () => {
              const result = await generateLabelFirst(processId, datasetId, {
                total: Number(total),
                mix: { approve: Number(approve), reject: Number(reject), escalate: Number(escalate) },
                messy_fraction: Number(messy) / 100,
              })
              if (result.queued) return 'Generating in the background -- cases appear as batches finish.'
              const r = result.result!
              return `Generated ${r.kept} cases the rules confirm (${r.discarded} discarded because the rules disagreed).`
            })
          }}
        >
          <strong>Generate cases with known answers</strong>
          <label>How many<input type="number" min={1} max={2000} value={total} onChange={(e) => setTotal(e.target.value)} /></label>
          <label>% approve<input type="number" min={0} value={approve} onChange={(e) => setApprove(e.target.value)} /></label>
          <label>% reject<input type="number" min={0} value={reject} onChange={(e) => setReject(e.target.value)} /></label>
          <label>% escalate<input type="number" min={0} value={escalate} onChange={(e) => setEscalate(e.target.value)} /></label>
          <label>% messy<input type="number" min={0} max={100} value={messy} onChange={(e) => setMessy(e.target.value)} /></label>
          <button type="submit" className="button-secondary" disabled={disabled}>{busy === 'generate' ? 'Generating...' : 'Generate'}</button>
        </form>
      )}

      <h3>Things that must hold whatever the answer</h3>
      <ul>
        {relations.map((r) => (
          <li key={r.id}>
            {r.name} <span className="meta">({r.kind}{r.direction ? `, ${r.direction.replace('_', ' ')}` : ''})</span>{' '}
            {canEdit && (
              <button type="button" className="link-button" disabled={disabled} onClick={() => run(r.id, async () => `Added ${(await generateVariants(processId, datasetId, r.id)).variants} variant cases.`)}>
                Make variants
              </button>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <form
          className="create-form"
          onSubmit={(e) => {
            e.preventDefault()
            const perturbation: Record<string, unknown> = { type: relType }
            if (relType === 'scale') perturbation.factor = Number(relAmount || 1)
            if (relType === 'add') perturbation.amount = Number(relAmount || 0)
            if (relType === 'set') perturbation.value = relAmount
            void run('relation', async () => {
              await createRelation(processId, datasetId, {
                name: relName || `${relField} ${relType}`,
                kind: relKind,
                field: relField,
                perturbation,
                direction: relKind === 'directional' ? relDirection : null,
              })
              return 'Relation added -- make variants to test it.'
            })
          }}
        >
          <label>Name<input value={relName} onChange={(e) => setRelName(e.target.value)} placeholder="Supplier name casing" /></label>
          <label>
            Kind
            <select value={relKind} onChange={(e) => setRelKind(e.target.value as 'invariance' | 'directional')}>
              <option value="invariance">Must not change the decision</option>
              <option value="directional">Must move the decision</option>
            </select>
          </label>
          <label>Field<input value={relField} onChange={(e) => setRelField(e.target.value)} required placeholder="supplier_name" /></label>
          <label>
            Change
            <select value={relType} onChange={(e) => setRelType(e.target.value)}>
              <option value="uppercase">UPPERCASE it</option>
              <option value="lowercase">lowercase it</option>
              <option value="titlecase">Title Case it</option>
              <option value="whitespace">Add spaces</option>
              <option value="scale">Multiply by</option>
              <option value="add">Add</option>
              <option value="set">Set to</option>
              <option value="remove">Remove it</option>
            </select>
          </label>
          {['scale', 'add', 'set'].includes(relType) && <label>Value<input value={relAmount} onChange={(e) => setRelAmount(e.target.value)} /></label>}
          {relKind === 'directional' && (
            <label>
              Toward
              <select value={relDirection} onChange={(e) => setRelDirection(e.target.value)}>
                <option value="toward_escalate">escalate</option>
                <option value="toward_reject">reject</option>
                <option value="toward_approve">approve</option>
              </select>
            </label>
          )}
          <button type="submit" className="button-secondary" disabled={disabled}>Add</button>
        </form>
      )}

      <h3>Needs a person ({queue.length})</h3>
      <p className="meta">Only the cases where the label, the rules, and the agent don't agree -- most important first.</p>
      {queue.length === 0 ? (
        <p className="empty-state">Nothing disagrees.</p>
      ) : (
        <ul>
          {queue.slice(0, 30).map((item) => (
            <li key={item.row_key}>
              {item.critical && <span className="badge confidence-low">Critical</span>}{' '}
              {Object.entries(item.inputs).slice(0, 3).map(([k, v]) => `${k}: ${String(v)}`).join(', ')} -- {item.disagreements.join('; ')}
            </li>
          ))}
        </ul>
      )}

      <h3>AI judges for free-text answers</h3>
      <p className="meta">
        A judge grades written answers against a rubric. It only counts toward a release once it agrees with people
        often enough on this dataset's human verdicts.
      </p>
      <ul>
        {rubrics.map((r) => (
          <li key={r.id}>
            <strong>{r.name}</strong> on <code>{r.field}</code> --{' '}
            {r.calibration?.agreement != null
              ? `agrees with people ${Math.round(r.calibration.agreement * 100)}% of the time on ${r.calibration.samples} cases`
              : 'not calibrated yet'}
            {r.gate_eligible ? <span className="badge status-done">Trusted for release</span> : <span className="badge">Not trusted yet</span>}{' '}
            {canEdit && (
              <button type="button" className="link-button" disabled={disabled} onClick={() => run(r.id, async () => {
                const updated = await calibrateJudge(processId, r.id, datasetId)
                return `Calibrated on ${updated.calibration?.samples ?? 0} cases.`
              })}>
                Calibrate on this dataset
              </button>
            )}
          </li>
        ))}
      </ul>
      {canEdit && (
        <form
          className="create-form"
          onSubmit={(e) => {
            e.preventDefault()
            void run('rubric', async () => {
              await createJudgeRubric(processId, { name: rubricName, field: rubricField, rubric: rubricText })
              return 'Judge added -- calibrate it before relying on it.'
            })
          }}
        >
          <label>Name<input value={rubricName} onChange={(e) => setRubricName(e.target.value)} required /></label>
          <label>Output field<input value={rubricField} onChange={(e) => setRubricField(e.target.value)} required placeholder="explanation" /></label>
          <label>Rubric<input value={rubricText} onChange={(e) => setRubricText(e.target.value)} required placeholder="Names the rule that led to the decision" /></label>
          <button type="submit" className="button-secondary" disabled={disabled}>Add judge</button>
        </form>
      )}
    </section>
  )
}
