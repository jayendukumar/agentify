import type { RunStep } from '../api/types'
import { stepDurationMs } from '../lib/runSteps'

function formatMs(ms: number): string {
  if (ms < 1000) return `${ms}ms`
  return `${(ms / 1000).toFixed(1)}s`
}

function formatJson(value: Record<string, unknown> | null): string | null {
  if (value === null || Object.keys(value).length === 0) return null
  return JSON.stringify(value, null, 2)
}

// Epic 18, US18.2: a step's full detail -- everything a RunStep carries,
// without depending on hidden model chain-of-thought to explain what
// happened. Follows the same <aside className="element-detail-panel">
// click-to-inspect convention as ElementDetailPanel/AgentDetailPanel/
// BlueprintDetailPanel.
export default function StepInspector({ step, allSteps }: { step: RunStep | null; allSteps: RunStep[] }) {
  if (!step) {
    return (
      <aside className="element-detail-panel" data-testid="step-inspector">
        <p className="meta">Select a step to see its full detail.</p>
      </aside>
    )
  }

  const durationMs = stepDurationMs(allSteps, step.seq)
  const args = formatJson(step.arguments)
  const result = formatJson(step.result)

  return (
    <aside className="element-detail-panel" data-testid="step-inspector">
      <div className="element-card">
        <div className="element-header">
          <span className="badge">{step.type.replace(/_/g, ' ').toLowerCase()}</span>
          {step.target && <span className="element-label">{step.target}</span>}
          {step.denied && <span className="badge status-failed">denied</span>}
        </div>
        <div className="element-meta">Step #{step.seq}</div>
        {step.node_id && <div className="element-meta">Node: {step.node_id}</div>}
        <div className="element-meta">At: {new Date(step.occurred_at).toLocaleTimeString()}</div>
        {durationMs !== null && <div className="element-meta">Duration: {formatMs(durationMs)}</div>}
        {step.decision && <div className="element-meta">Decision: {step.decision}</div>}
        {step.tokens != null && <div className="element-meta">Tokens: {step.tokens}</div>}
        {step.cost_usd != null && <div className="element-meta">Cost: ${step.cost_usd.toFixed(5)}</div>}
        {step.static_fallback && (
          <div className="element-meta">Fell back to a fabricated response (no static rule matched)</div>
        )}
        {step.reason && <div className="element-meta">Reason: {step.reason}</div>}
        {args && (
          <details className="element-sources" open>
            <summary>Arguments</summary>
            <pre>{args}</pre>
          </details>
        )}
        {result && (
          <details className="element-sources" open>
            <summary>Result</summary>
            <pre>{result}</pre>
          </details>
        )}
      </div>
    </aside>
  )
}
