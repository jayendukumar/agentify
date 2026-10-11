import type { TwinRun, TwinScenario } from '../api/types'
import { isTerminalRunStatus } from '../lib/runStatus'
import RunDetailView from './RunDetailView'

// Epic 26, US26.1: results headlined in business terms -- "did it behave
// as expected" -- with each failure as a plain sentence and the technical
// trace, cost, and tokens tucked under "Details".

type Verdict = 'passed' | 'failed' | 'error' | 'running' | 'waiting' | 'not-run'

function verdictOf(run: TwinRun | undefined): Verdict {
  if (!run) return 'not-run'
  if (run.status === 'WAITING_FOR_HUMAN') return 'waiting'
  if (!isTerminalRunStatus(run.status)) return 'running'
  if (run.status === 'COMPLETED') return run.graded_passed === false ? 'failed' : 'passed'
  return 'error'
}

const VERDICT_TEXT: Record<Verdict, string> = {
  passed: 'Behaved as expected',
  failed: "Didn't behave as expected",
  error: "Couldn't finish",
  running: 'Running…',
  waiting: 'Waiting for a person to decide',
  'not-run': 'Not run yet',
}

const VERDICT_CLASS: Record<Verdict, string> = {
  passed: 'badge status-done',
  failed: 'badge status-failed',
  error: 'badge status-failed',
  running: 'badge status-processing',
  waiting: 'badge status-processing',
  'not-run': 'badge',
}

export default function TwinSuiteResults({
  processId,
  scenarios,
  runsByScenario,
  onRun,
  onRunAll,
  onDelete,
  startingIds,
  canEdit,
}: {
  processId: string
  scenarios: TwinScenario[]
  runsByScenario: Record<string, TwinRun>
  onRun: (scenarioId: string) => void
  onRunAll: () => void
  onDelete: (scenarioId: string) => void
  startingIds: Set<string>
  canEdit: boolean
}) {
  if (scenarios.length === 0) {
    return <p className="meta">No situations saved for this agent yet.</p>
  }

  const verdicts = scenarios.map((s) => verdictOf(runsByScenario[s.id]))
  const finished = verdicts.filter((v) => v === 'passed' || v === 'failed' || v === 'error').length
  const passed = verdicts.filter((v) => v === 'passed').length
  const inFlight = verdicts.some((v) => v === 'running')
  const cost = scenarios.reduce((sum, s) => sum + (runsByScenario[s.id]?.total_cost_usd ?? 0), 0)

  return (
    <section className="suite-results" aria-label="Test results" data-testid="suite-results">
      <div className="suite-headline">
        <div>
          <p className="suite-headline-number">
            {finished === 0 ? '—' : `${passed} of ${finished}`}
          </p>
          <p className="meta">
            {finished === 0
              ? 'situations run so far'
              : `situation${finished === 1 ? '' : 's'} behaved as expected${
                  finished < scenarios.length ? ` (${scenarios.length - finished} not finished)` : ''
                }`}
          </p>
        </div>
        <div className="suite-headline-actions">
          <button type="button" className="button-primary" onClick={onRunAll} disabled={!canEdit || inFlight || startingIds.size > 0}>
            {inFlight ? 'Running…' : finished === 0 ? 'Run all situations' : 'Run all again'}
          </button>
          {cost > 0 && <span className="meta">Last runs cost ${cost.toFixed(4)} in AI usage</span>}
        </div>
      </div>
      {finished > 0 && (
        <div
          className="suite-bar"
          role="img"
          aria-label={`${passed} passed, ${finished - passed} did not, ${scenarios.length - finished} not finished`}
        >
          <span className="suite-bar-pass" style={{ flex: passed }} />
          <span className="suite-bar-fail" style={{ flex: finished - passed }} />
          <span className="suite-bar-rest" style={{ flex: scenarios.length - finished }} />
        </div>
      )}

      <ul className="suite-list">
        {scenarios.map((scenario, i) => {
          const run = runsByScenario[scenario.id]
          const verdict = verdicts[i]
          const title = scenario.story?.title ?? scenario.name
          return (
            <li key={scenario.id} className={`suite-item suite-item-${verdict}`} data-testid="suite-item">
              <div className="suite-item-header">
                <strong>{title}</strong>
                <span className={VERDICT_CLASS[verdict]}>{VERDICT_TEXT[verdict]}</span>
              </div>
              {scenario.story && (scenario.story.given || scenario.story.when) && (
                <p className="meta">
                  {[scenario.story.given, scenario.story.when].filter(Boolean).join(' — ')}
                </p>
              )}
              {verdict === 'failed' && run && run.deviations.length > 0 && (
                <ul className="suite-deviations">
                  {run.deviations.map((d, j) => (
                    <li key={j}>{d.reason}</li>
                  ))}
                </ul>
              )}
              {verdict === 'error' && run && (
                <p className="error">{run.deviations[0]?.reason ?? `The run ended with status ${run.status}.`}</p>
              )}
              {verdict === 'waiting' && (
                <p className="meta">Open Advanced to approve or reject this run's pending decision.</p>
              )}
              <div className="suite-item-actions">
                <button
                  type="button"
                  className="button-secondary"
                  onClick={() => onRun(scenario.id)}
                  disabled={!canEdit || startingIds.has(scenario.id) || verdict === 'running'}
                >
                  {startingIds.has(scenario.id) ? 'Starting…' : run ? 'Run again' : 'Run'}
                </button>
                <button type="button" className="link-button" onClick={() => onDelete(scenario.id)} disabled={!canEdit}>
                  Remove
                </button>
              </div>
              {run && isTerminalRunStatus(run.status) && (
                <details className="suite-details">
                  <summary>Details</summary>
                  <p className="meta">
                    {run.steps.length} step(s) · {run.turns_used} turn(s) · {run.total_tokens} tokens ·{' '}
                    {run.total_cost_usd !== null ? `$${run.total_cost_usd.toFixed(4)}` : 'cost unknown'}
                  </p>
                  <RunDetailView run={run} processId={processId} staticSubjectLabel={title} />
                </details>
              )}
            </li>
          )
        })}
      </ul>
    </section>
  )
}
