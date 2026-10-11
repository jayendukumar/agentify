import { useEffect, useMemo, useRef, useState } from 'react'
import {
  ApiError,
  createScenario,
  createTwinEnvironment,
  deleteScenario,
  describeSituation,
  getTestBuilderContext,
  getTwinRun,
  listScenarios,
  listTwinEnvironments,
  listTwinRuns,
  startTwinRun,
  suggestSituations,
  updateTwinEnvironment,
} from '../api/client'
import type {
  AgentArtifact,
  GeneratedRecord,
  ReviewerPersona,
  Situation,
  TestBuilderContext,
  TwinEnvironment,
  TwinRun,
  TwinScenario,
} from '../api/types'
import { useAuth } from '../auth/AuthContext'
import type { AgentGroup } from '../lib/blueprintLabels'
import { isTerminalRunStatus } from '../lib/runStatus'
import { blankSituation, compileSituation, coveredTargetIds } from '../lib/twinSituations'
import TestDataKitchen from './TestDataKitchen'
import TwinExpectationsEditor from './TwinExpectationsEditor'
import TwinSceneEditor from './TwinSceneEditor'
import TwinSituationCard from './TwinSituationCard'
import TwinSuiteResults from './TwinSuiteResults'

// Epic 24: the guided Digital Twin test builder -- a five-step flow where
// the system proposes tests from the agentic blueprint and a business user
// chooses, tweaks and confirms them. Everything compiles down to ordinary
// TwinScenarios (see lib/twinSituations.ts), so runs, grading, evaluation
// and the Advanced view all keep working unchanged.

const STEPS = [
  { label: 'Pick an agent', description: 'Which automated step to test' },
  { label: 'Choose situations', description: 'What cases to try' },
  { label: 'Set the scene', description: 'Data, systems and reviewers' },
  { label: 'What should happen', description: 'How to judge the outcome' },
  { label: 'Run & review', description: 'Save, run and read results' },
]

const POLL_INTERVAL_MS = 2000

const CHECKPOINT_LABELS: Record<string, string> = {
  none: 'Runs without human review',
  review_before_action: 'A person reviews before it acts',
  review_after_action: 'A person reviews after it acts',
  escalation_on_exception: 'Escalates exceptions to a person',
}

type DraftState = 'proposed' | 'kept' | 'skipped'
interface Draft {
  key: number
  situation: Situation
  state: DraftState
}

function errorText(err: unknown, fallback: string): string {
  return err instanceof ApiError ? err.message : err instanceof Error ? err.message : fallback
}

// Epic 23's release gate refuses a run until the checklist is satisfied --
// surfaced on the agent card up front, rather than as an error after
// "Save & run". "Test scenarios" is excluded: saving situations here is
// exactly what satisfies it.
function setupGaps(artifact: AgentArtifact): string[] {
  return (artifact.checklist ?? [])
    .filter((item) => !item.satisfied && item.key !== 'test_scenarios')
    .map((item) => item.label)
}

function kitName(agentName: string): string {
  return `${agentName} test kit`
}

export default function TwinTestBuilder({ processId, groups }: { processId: string; groups: AgentGroup[] }) {
  const { user } = useAuth()
  const canEdit = user?.role === 'editor'
  const artifacts = useMemo(
    () => groups.map((g) => g.artifact).filter((a): a is AgentArtifact => a !== null),
    [groups],
  )

  const [step, setStep] = useState(0)
  const [artifactId, setArtifactId] = useState<string | null>(null)
  const artifact = artifacts.find((a) => a.id === artifactId) ?? null

  const [context, setContext] = useState<TestBuilderContext | null>(null)
  const [scenarios, setScenarios] = useState<TwinScenario[]>([])
  const [runsByScenario, setRunsByScenario] = useState<Record<string, TwinRun>>({})
  const [kit, setKit] = useState<TwinEnvironment | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const [drafts, setDrafts] = useState<Draft[]>([])
  const nextKey = useRef(1)
  const [suggesting, setSuggesting] = useState<string | null>(null)
  const [description, setDescription] = useState('')
  const [describing, setDescribing] = useState(false)

  const [shelf, setShelf] = useState<GeneratedRecord[]>([])
  const [personas, setPersonas] = useState<ReviewerPersona[]>([])
  const [savingKit, setSavingKit] = useState(false)
  const [kitSaved, setKitSaved] = useState(false)

  const [saving, setSaving] = useState(false)
  const [startingIds, setStartingIds] = useState<Set<string>>(new Set())
  const pollTimers = useRef<Record<string, ReturnType<typeof setInterval>>>({})

  useEffect(() => {
    const timers = pollTimers.current
    return () => Object.values(timers).forEach(clearInterval)
  }, [])

  const kept = drafts.filter((d) => d.state === 'kept')
  const covered = coveredTargetIds(scenarios, kept.map((d) => d.situation))

  function addDrafts(situations: Situation[], state: DraftState = 'proposed') {
    setDrafts((prev) => [...prev, ...situations.map((situation) => ({ key: nextKey.current++, situation, state }))])
  }

  function updateDraft(key: number, situation: Situation) {
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, situation } : d)))
  }

  function setDraftState(key: number, state: DraftState) {
    setDrafts((prev) => prev.map((d) => (d.key === key ? { ...d, state: d.state === state ? 'proposed' : state } : d)))
  }

  function startPolling(scenarioId: string, runId: string) {
    clearInterval(pollTimers.current[scenarioId])
    const timer = setInterval(async () => {
      try {
        const run = await getTwinRun(processId, runId)
        setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
        if (isTerminalRunStatus(run.status) || run.status === 'WAITING_FOR_HUMAN') {
          clearInterval(timer)
          delete pollTimers.current[scenarioId]
        }
      } catch (err) {
        clearInterval(timer)
        delete pollTimers.current[scenarioId]
        setError(errorText(err, 'Lost connection while watching a run'))
      }
    }, POLL_INTERVAL_MS)
    pollTimers.current[scenarioId] = timer
  }

  async function chooseAgent(id: string) {
    const chosen = artifacts.find((a) => a.id === id)
    if (!chosen) return
    Object.values(pollTimers.current).forEach(clearInterval)
    pollTimers.current = {}
    setArtifactId(id)
    setLoading(true)
    setError(null)
    setDrafts([])
    setKitSaved(false)
    try {
      const [ctx, scenarioList, runList, environments] = await Promise.all([
        getTestBuilderContext(processId, id),
        listScenarios(processId, id),
        listTwinRuns(processId, id),
        listTwinEnvironments(processId),
      ])
      setContext(ctx)
      setScenarios(scenarioList)
      const latest: Record<string, TwinRun> = {}
      for (const run of runList) if (!latest[run.scenario_id]) latest[run.scenario_id] = run
      setRunsByScenario(latest)
      for (const run of Object.values(latest)) {
        if (!isTerminalRunStatus(run.status) && run.status !== 'WAITING_FOR_HUMAN') startPolling(run.scenario_id, run.id)
      }
      const existingKit = environments.find((env) => env.name === kitName(chosen.definition.name)) ?? null
      setKit(existingKit)
      const savedRecords = existingKit?.datasets.records
      setShelf(Array.isArray(savedRecords) ? (savedRecords as GeneratedRecord[]) : [])
      setPersonas(Object.values(existingKit?.personas ?? {}) as ReviewerPersona[])
      setStep(scenarioList.length > 0 ? 4 : 1)
    } catch (err) {
      setError(errorText(err, 'Could not load this agent'))
    } finally {
      setLoading(false)
    }
  }

  async function suggest(focus: string | null, key: string, count = 6) {
    if (!artifactId) return
    setSuggesting(key)
    setError(null)
    try {
      addDrafts(await suggestSituations(processId, artifactId, { count, focus, already_covered: [...covered] }))
    } catch (err) {
      setError(errorText(err, 'Could not suggest situations -- please try again'))
    } finally {
      setSuggesting(null)
    }
  }

  async function describe() {
    if (!artifactId || description.trim() === '') return
    setDescribing(true)
    setError(null)
    try {
      addDrafts([await describeSituation(processId, artifactId, description.trim())], 'kept')
      setDescription('')
    } catch (err) {
      setError(errorText(err, 'Could not turn that into a situation -- try rephrasing it'))
    } finally {
      setDescribing(false)
    }
  }

  async function saveKit(): Promise<TwinEnvironment | null> {
    if (!artifact || !context) return null
    setSavingKit(true)
    setError(null)
    try {
      const body = {
        name: kitName(artifact.definition.name),
        systems: context.systems,
        datasets: { ...(kit?.datasets ?? {}), records: shelf },
        personas: Object.fromEntries(personas.map((p) => [p.name, p])),
        events: kit?.events ?? [],
        faults: kit?.faults ?? {},
      }
      const saved = kit ? await updateTwinEnvironment(processId, kit.id, body) : await createTwinEnvironment(processId, body)
      setKit(saved)
      setKitSaved(true)
      return saved
    } catch (err) {
      setError(errorText(err, 'Could not save the test kit'))
      return null
    } finally {
      setSavingKit(false)
    }
  }

  async function runScenario(scenarioId: string) {
    setStartingIds((prev) => new Set(prev).add(scenarioId))
    setError(null)
    try {
      const run = await startTwinRun(processId, scenarioId)
      setRunsByScenario((prev) => ({ ...prev, [scenarioId]: run }))
      startPolling(scenarioId, run.id)
    } catch (err) {
      setError(errorText(err, 'Could not start the run'))
    } finally {
      setStartingIds((prev) => {
        const next = new Set(prev)
        next.delete(scenarioId)
        return next
      })
    }
  }

  async function saveAndRun() {
    if (!artifactId || !context) return
    setSaving(true)
    setError(null)
    try {
      const kitForScenarios = shelf.length > 0 || personas.length > 0 ? (kitSaved ? kit : await saveKit()) : kit
      const created: TwinScenario[] = []
      for (const draft of kept) {
        created.push(
          await createScenario(processId, artifactId, compileSituation(draft.situation, context, kitForScenarios?.id ?? null)),
        )
      }
      setScenarios((prev) => [...prev, ...created])
      setDrafts((prev) => prev.filter((d) => d.state !== 'kept'))
      for (const scenario of created) await runScenario(scenario.id)
    } catch (err) {
      setError(errorText(err, 'Could not save the situations'))
    } finally {
      setSaving(false)
    }
  }

  async function removeScenario(scenarioId: string) {
    if (!window.confirm('Remove this situation from the suite? This cannot be undone.')) return
    try {
      await deleteScenario(processId, scenarioId)
      setScenarios((prev) => prev.filter((s) => s.id !== scenarioId))
    } catch (err) {
      setError(errorText(err, 'Could not remove the situation'))
    }
  }

  if (artifacts.length === 0) {
    return (
      <div className="twin-empty">
        <h3>No agents to test yet</h3>
        <p className="meta">Generate an agent from the Agents tab first -- then come back here to test it.</p>
      </div>
    )
  }

  const canEnter = (index: number) => index === 0 || (context !== null && (index < 2 || index === 4 || kept.length > 0))
  const targets = context?.coverage_targets ?? []
  const coveredCount = targets.filter((t) => covered.has(t.id)).length

  return (
    <div className="twin-builder" data-testid="twin-test-builder">
      <ol className="wizard-steps" aria-label="Test setup steps">
        {STEPS.map((s, i) => (
          <li key={s.label} className={`wizard-step${i === step ? ' wizard-step-current' : i < step ? ' wizard-step-done' : ''}`}>
            <button
              type="button"
              onClick={() => setStep(i)}
              disabled={!canEnter(i)}
              aria-current={i === step ? 'step' : undefined}
            >
              <span className="wizard-step-marker">{i < step ? '✓' : i + 1}</span>
              <span className="wizard-step-text">
                <span className="wizard-step-label">{s.label}</span>
                <span className="wizard-step-description">{s.description}</span>
              </span>
            </button>
          </li>
        ))}
      </ol>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {loading && <p className="meta">Reading the blueprint for this agent…</p>}

      {step === 0 && (
        <section aria-label="Pick an agent">
          <h3>Which agent do you want to test?</h3>
          <p className="meta">
            Tests run in a digital twin, a safe simulated copy of your process. No real system is called and no real
            person is contacted.
          </p>
          <div className="agent-card-grid">
            {artifacts.map((a) => (
              <button
                key={a.id}
                type="button"
                className={`agent-card${a.id === artifactId ? ' agent-card-selected' : ''}`}
                onClick={() => chooseAgent(a.id)}
                disabled={loading}
              >
                <span className="agent-card-name">{a.definition.name}</span>
                <span className="agent-card-purpose">{a.definition.purpose}</span>
                <span className="chip-row">
                  {a.definition.tools_systems_needed.map((s) => (
                    <span key={s} className="badge">
                      {s}
                    </span>
                  ))}
                </span>
                <span className="meta">{CHECKPOINT_LABELS[a.definition.human_checkpoint] ?? ''}</span>
                {setupGaps(a).length > 0 && (
                  <span className="agent-card-warning">
                    Needs setup before it can run: {setupGaps(a).join(', ')}. Regenerate it from the Agents tab.
                  </span>
                )}
              </button>
            ))}
          </div>
        </section>
      )}

      {step === 1 && context && (
        <section aria-label="Choose situations">
          <div className="coverage-panel" data-testid="coverage-panel">
            <div className="coverage-header">
              <h3>What could happen to {context.agent_name}?</h3>
              <span className="coverage-count">
                {coveredCount} of {targets.length} covered
              </span>
            </div>
            <div
              className="coverage-meter"
              role="progressbar"
              aria-label="Test coverage"
              aria-valuemin={0}
              aria-valuemax={targets.length}
              aria-valuenow={coveredCount}
            >
              <span style={{ width: `${targets.length ? (coveredCount / targets.length) * 100 : 0}%` }} />
            </div>
            <p className="meta">
              Based on the blueprint. Click anything not yet covered to get a situation that tests it.
            </p>
            <div className="chip-row">
              {targets.map((t) =>
                covered.has(t.id) ? (
                  <span key={t.id} className="badge coverage-chip coverage-chip-done">
                    ✓ {t.label}
                  </span>
                ) : (
                  <button
                    key={t.id}
                    type="button"
                    className="button-secondary chip-button"
                    disabled={!canEdit || suggesting !== null}
                    onClick={() => suggest(t.label, t.id, 2)}
                  >
                    {suggesting === t.id ? 'Thinking…' : `+ ${t.label}`}
                  </button>
                ),
              )}
            </div>
          </div>

          <div className="situation-sources">
            <div className="situation-source">
              <h4>Let AI propose situations</h4>
              <p className="meta">Realistic stories built from this agent's steps, decisions, systems and reviewers.</p>
              <button type="button" className="button-primary" onClick={() => suggest(null, 'all')} disabled={!canEdit || suggesting !== null}>
                {suggesting === 'all' ? 'Thinking up situations…' : '✨ Suggest situations'}
              </button>
            </div>
            <div className="situation-source">
              <h4>Describe your own</h4>
              <label className="sr-only" htmlFor="describe-situation">
                Describe a situation
              </label>
              <textarea
                id="describe-situation"
                rows={2}
                value={description}
                placeholder="e.g. A large invoice arrives on a Friday evening while the ERP is down"
                onChange={(e) => setDescription(e.target.value)}
              />
              <div className="chip-row">
                <button
                  type="button"
                  className="button-primary"
                  onClick={describe}
                  disabled={!canEdit || describing || description.trim().length < 3}
                >
                  {describing ? 'Writing it up…' : 'Create situation'}
                </button>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => addDrafts([blankSituation(context)], 'kept')}
                >
                  Start from blank
                </button>
              </div>
            </div>
          </div>

          {drafts.length > 0 && (
            <>
              <div className="situation-list-header">
                <h4>
                  Proposed situations <span className="meta">({kept.length} kept)</span>
                </h4>
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => setDrafts((prev) => prev.map((d) => (d.state === 'skipped' ? d : { ...d, state: 'kept' })))}
                >
                  Keep all
                </button>
              </div>
              <div className="situation-grid">
                {drafts.map((d) => (
                  <TwinSituationCard
                    key={d.key}
                    situation={d.situation}
                    targets={targets}
                    state={d.state}
                    onKeep={() => setDraftState(d.key, 'kept')}
                    onSkip={() => setDraftState(d.key, 'skipped')}
                  />
                ))}
              </div>
            </>
          )}

          {scenarios.length > 0 && (
            <p className="meta">
              This agent already has {scenarios.length} saved situation{scenarios.length === 1 ? '' : 's'}.{' '}
              <button type="button" className="link-button" onClick={() => setStep(4)}>
                See results
              </button>
            </p>
          )}

          <div className="wizard-nav">
            <button type="button" className="button-secondary" onClick={() => setStep(0)}>
              Back
            </button>
            <button type="button" className="button-primary" onClick={() => setStep(2)} disabled={kept.length === 0}>
              Next: set the scene ({kept.length})
            </button>
          </div>
        </section>
      )}

      {step === 2 && context && (
        <section aria-label="Set the scene">
          <h3>Set the scene</h3>
          <p className="meta">
            Give each situation its data, decide how the systems behave, and who reviews. Use the kitchen on the right
            to generate data.
          </p>
          <div className="scene-layout">
            <div className="scene-list">
              {kept.map((d) => (
                <TwinSceneEditor
                  key={d.key}
                  situation={d.situation}
                  context={context}
                  personas={personas}
                  onChange={(next) => updateDraft(d.key, next)}
                  onRemove={() => setDraftState(d.key, 'skipped')}
                />
              ))}
            </div>
            <TestDataKitchen
              processId={processId}
              context={context}
              shelf={shelf}
              onShelfChange={(next) => {
                setShelf(next)
                setKitSaved(false)
              }}
              personas={personas}
              onPersonasChange={(next) => {
                setPersonas(next)
                setKitSaved(false)
              }}
              situationTitles={kept.map((d) => d.situation.story.title)}
              onUseRecord={(index, record) => {
                const target = kept[index]
                if (target) updateDraft(target.key, { ...target.situation, inputs: { ...record } })
              }}
              onSaveKit={() => void saveKit()}
              savingKit={savingKit}
              kitSaved={kitSaved}
              canEdit={canEdit}
            />
          </div>
          <div className="wizard-nav">
            <button type="button" className="button-secondary" onClick={() => setStep(1)}>
              Back
            </button>
            <button type="button" className="button-primary" onClick={() => setStep(3)} disabled={kept.length === 0}>
              Next: what should happen
            </button>
          </div>
        </section>
      )}

      {step === 3 && context && (
        <section aria-label="What should happen">
          <h3>What should happen?</h3>
          <p className="meta">
            Pre-filled from the blueprint. Tick what a correct agent must do. Extra lookups or retries are fine, as
            long as these happen in this order.
          </p>
          <div className="expectations-list">
            {kept.map((d) => (
              <TwinExpectationsEditor
                key={d.key}
                situation={d.situation}
                context={context}
                onChange={(next) => updateDraft(d.key, next)}
              />
            ))}
          </div>
          <div className="wizard-nav">
            <button type="button" className="button-secondary" onClick={() => setStep(2)}>
              Back
            </button>
            <button type="button" className="button-primary" onClick={() => setStep(4)} disabled={kept.length === 0}>
              Next: review &amp; run
            </button>
          </div>
        </section>
      )}

      {step === 4 && context && (
        <section aria-label="Run and review">
          {kept.length > 0 && (
            <div className="save-run-panel">
              <h3>Ready to run {kept.length} new situation{kept.length === 1 ? '' : 's'}</h3>
              <ul className="meta">
                {kept.map((d) => (
                  <li key={d.key}>{d.situation.story.title}</li>
                ))}
              </ul>
              <button type="button" className="button-primary" onClick={saveAndRun} disabled={!canEdit || saving}>
                {saving ? 'Saving & starting…' : `Save & run ${kept.length} situation${kept.length === 1 ? '' : 's'}`}
              </button>
            </div>
          )}
          <div className="situation-list-header">
            <h3>{context.agent_name}: test suite</h3>
            <button type="button" className="button-secondary" onClick={() => setStep(1)}>
              + Add situations
            </button>
          </div>
          <TwinSuiteResults
            processId={processId}
            scenarios={scenarios}
            runsByScenario={runsByScenario}
            onRun={(id) => void runScenario(id)}
            onRunAll={() => scenarios.forEach((s) => void runScenario(s.id))}
            onDelete={(id) => void removeScenario(id)}
            startingIds={startingIds}
            canEdit={canEdit}
          />
        </section>
      )}
    </div>
  )
}
