import { useEffect, useMemo, useRef, useState } from 'react'
import { ApiError, getRunSnapshot } from '../api/client'
import type { OrchestrationRun, RunSnapshotView, RunStep, TwinRun } from '../api/types'
import BlueprintCanvas from './BlueprintCanvas'
import StepInspector from './StepInspector'
import { isTerminalRunStatus } from '../lib/runStatus'
import { cumulativeAt, narrateStep, stepsForView, type RunView } from '../lib/runSteps'
import { handleTabKeyDown } from '../lib/tabKeyboard'

const REPLAY_INTERVAL_MS = 900
const VIEWS: { id: RunView; label: string }[] = [
  { id: 'process', label: 'Process' },
  { id: 'agent', label: 'Agent' },
  { id: 'technical', label: 'Technical' },
]

function stepRowLabel(step: RunStep, view: RunView): string {
  if (view === 'process') return narrateStep(step) ?? step.type
  return step.target ? `${step.type.replace(/_/g, ' ').toLowerCase()} · ${step.target}` : step.type.replace(/_/g, ' ').toLowerCase()
}

function noop() {
  // BlueprintCanvas requires onDiagramReady; this embedded read-only
  // instance has no separate label-lookup consumer.
}

// Epic 18: the shared, kind-agnostic run-observability view -- US18.1
// (live step list, via the caller's polling already updating `run`),
// US18.2 (StepInspector), US18.3 (Process/Agent/Technical view switch over
// one shared step list, all driven by one `selectedSeq`), US18.4 (BPMN
// canvas highlight, orchestration runs only -- see this module's docstring
// in the epic's plan for why a twin run gets a static subject line
// instead), US18.5 (replay scrubber, terminal runs only).
export default function RunDetailView({
  run,
  processId,
  canvasXml,
  staticSubjectLabel,
}: {
  run: TwinRun | OrchestrationRun
  processId: string
  canvasXml?: string | null
  staticSubjectLabel?: string
}) {
  const [view, setView] = useState<RunView>('process')
  const [selectedSeq, setSelectedSeq] = useState<number | null>(null)
  const [playing, setPlaying] = useState(false)
  const playTimer = useRef<ReturnType<typeof setInterval> | null>(null)

  // Epic 22, US22.2/US22.3: fetched lazily, once, when the reproducibility
  // section is first opened -- not part of the run object itself (a
  // separate concern, and most viewers never open it).
  const [snapshotView, setSnapshotView] = useState<RunSnapshotView | null>(null)
  const [snapshotError, setSnapshotError] = useState<string | null>(null)
  const [snapshotLoading, setSnapshotLoading] = useState(false)

  function loadSnapshotOnce() {
    if (snapshotView || snapshotLoading) return
    setSnapshotLoading(true)
    setSnapshotError(null)
    getRunSnapshot(processId, run.id)
      .then(setSnapshotView)
      .catch((err) => setSnapshotError(err instanceof ApiError ? err.message : 'Failed to load the run snapshot'))
      .finally(() => setSnapshotLoading(false))
  }

  const steps = run.steps
  const terminal = isTerminalRunStatus(run.status)
  const latestSeq = steps.length > 0 ? steps[steps.length - 1].seq : null
  // Auto-follow the latest step while live, until the viewer picks one
  // themselves -- a manual selection during a live run is not overwritten
  // by newly-arriving steps.
  const effectiveSeq = selectedSeq ?? latestSeq
  const selectedStep = effectiveSeq === null ? null : steps.find((s) => s.seq === effectiveSeq) ?? null

  const rows = useMemo(() => stepsForView(steps, view), [steps, view])
  const cumulative = useMemo(
    () => (effectiveSeq === null ? null : cumulativeAt(steps, effectiveSeq)),
    [steps, effectiveSeq],
  )

  useEffect(() => {
    if (!playing) return
    playTimer.current = setInterval(() => {
      setSelectedSeq((prev) => {
        const current = prev ?? 0
        if (latestSeq === null || current >= latestSeq) {
          setPlaying(false)
          return prev
        }
        return current + 1
      })
    }, REPLAY_INTERVAL_MS)
    return () => {
      if (playTimer.current) clearInterval(playTimer.current)
    }
  }, [playing, latestSeq])

  const markers = selectedStep?.node_id ? { [selectedStep.node_id]: 'run-node-active' } : {}

  return (
    <div className="run-detail-view" data-testid="run-detail-view">
      {canvasXml ? (
        <div className="run-detail-canvas">
          <BlueprintCanvas xml={canvasXml} markers={markers} onSelectionChange={() => {}} onDiagramReady={noop} />
        </div>
      ) : (
        staticSubjectLabel && <p className="meta run-detail-subject">{staticSubjectLabel}</p>
      )}

      <div className="tab-bar" role="tablist" aria-label="Run views" onKeyDown={handleTabKeyDown}>
        {VIEWS.map((v) => (
          <button
            key={v.id}
            type="button"
            role="tab"
            aria-selected={view === v.id}
            tabIndex={view === v.id ? 0 : -1}
            className={`tab-button${view === v.id ? ' tab-button-active' : ''}`}
            onClick={() => setView(v.id)}
          >
            {v.label}
          </button>
        ))}
      </div>

      <div className="run-detail-body">
        <ol className="run-step-list" data-testid="run-step-list">
          {rows.length === 0 && <li className="meta">No steps in this view yet.</li>}
          {rows.map((step) => (
            <li key={step.seq}>
              <button
                type="button"
                className={`run-step-row${step.seq === effectiveSeq ? ' run-step-row-selected' : ''}`}
                onClick={() => setSelectedSeq(step.seq)}
              >
                <span className="meta">#{step.seq}</span> {stepRowLabel(step, view)}
              </button>
            </li>
          ))}
        </ol>
        <StepInspector step={selectedStep} allSteps={steps} />
      </div>

      {/* Epic 19, US19.1: twelve independently-visible evaluation dimensions,
          computed once the run reaches a terminal state -- null for a
          still-running run or one persisted before this epic. */}
      {run.evaluation && (
        <div className="run-evaluation" data-testid="run-evaluation">
          <h4>
            Evaluation ({run.evaluation.passed_count}/{run.evaluation.total_count} dimensions passed)
          </h4>
          <ul className="run-evaluation-list">
            {run.evaluation.dimensions.map((d) => (
              <li key={d.name}>
                <span className={`badge ${d.passed ? 'status-done' : 'status-failed'}`}>
                  {d.name.replace(/_/g, ' ')}
                </span>
                <span className="meta">{d.detail}</span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Epic 22, US22.2/US22.3: the exact versioned configuration this run
          used, plus the three reproducibility tiers -- never let "we can
          reconstruct this run's config" be read as "re-running it produces
          the same transcript." */}
      <details className="run-reproducibility" data-testid="run-reproducibility" onToggle={(e) => e.currentTarget.open && loadSnapshotOnce()}>
        <summary>Reproducibility</summary>
        {snapshotLoading && <p className="meta">Loading...</p>}
        {snapshotError && <p className="error" role="alert">{snapshotError}</p>}
        {snapshotView && (
          <>
            <ul className="run-reproducibility-tiers">
              {Object.entries(snapshotView.reproducibility).map(([tier, level]) => (
                <li key={tier}>
                  <span className={`badge ${level === 'guaranteed' ? 'status-done' : 'status-processing'}`}>{tier}: {level.replace('_', ' ')}</span>
                  <p className="meta">{snapshotView.explanation[tier]}</p>
                </li>
              ))}
            </ul>
            {snapshotView.snapshot ? (
              <pre className="run-reproducibility-snapshot">{JSON.stringify(snapshotView.snapshot, null, 2)}</pre>
            ) : (
              <p className="meta">No configuration snapshot was recorded for this run (it predates this feature).</p>
            )}
          </>
        )}
      </details>

      {terminal && steps.length > 0 && (
        <div className="run-replay-controls" data-testid="run-replay-controls">
          <button
            type="button"
            onClick={() => setSelectedSeq(Math.max(0, (effectiveSeq ?? 0) - 1))}
            disabled={(effectiveSeq ?? 0) <= 0}
          >
            ⏮
          </button>
          <button type="button" onClick={() => setPlaying((p) => !p)} disabled={effectiveSeq === latestSeq && !playing}>
            {playing ? '⏸' : '▶'}
          </button>
          <button
            type="button"
            onClick={() => setSelectedSeq(Math.min(latestSeq ?? 0, (effectiveSeq ?? 0) + 1))}
            disabled={latestSeq === null || effectiveSeq === latestSeq}
          >
            ⏭
          </button>
          <input
            type="range"
            min={0}
            max={latestSeq ?? 0}
            value={effectiveSeq ?? 0}
            onChange={(e) => setSelectedSeq(Number(e.target.value))}
            aria-label="Replay position"
          />
          {cumulative && (
            <span className="meta">
              at this point: {cumulative.tokens} tokens ·{' '}
              {cumulative.costUsd !== null ? `$${cumulative.costUsd.toFixed(4)}` : 'cost unknown'} ·{' '}
              {(cumulative.elapsedMs / 1000).toFixed(1)}s elapsed
            </span>
          )}
        </div>
      )}
    </div>
  )
}
