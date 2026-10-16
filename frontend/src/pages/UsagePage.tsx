import { Fragment, useEffect, useState } from 'react'
import { ApiError, getUsage } from '../api/client'
import type { TenantLimits, UsageOverview } from '../api/types'

// Epic 38, US38.9: what this organization's LLM calls cost, month by
// month, against the limits the hosting company set for it.

function usd(value: number): string {
  return value < 0.01 && value > 0 ? `$${value.toFixed(4)}` : `$${value.toFixed(2)}`
}

function tokens(value: number): string {
  return value.toLocaleString()
}

function limitText(limits: TenantLimits, key: keyof TenantLimits): string {
  const value = limits[key]
  if (value === null) return 'No limit'
  return key === 'llm_monthly_budget_usd' ? usd(value) : String(value)
}

const BUDGET_TEXT: Record<UsageOverview['budget']['status'], string> = {
  unlimited: 'No monthly budget is set.',
  ok: 'Within budget.',
  warning: 'Over 80% of the monthly budget. LLM calls stop when it is used up.',
  stopped: 'The monthly budget is used up: LLM calls are refused, and batches stop, until next month or until the budget is raised.',
}

export default function UsagePage() {
  const [usage, setUsage] = useState<UsageOverview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [open, setOpen] = useState<string | null>(null)

  useEffect(() => {
    getUsage()
      .then(setUsage)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load usage'))
  }, [])

  if (error) return <div className="page"><h1>Usage</h1><p className="error" role="alert">{error}</p></div>
  if (usage === null) return <div className="page"><p className="loading-state" role="status">Loading...</p></div>

  const { budget, limits } = usage
  return (
    <div className="page">
      <h1>Usage</h1>
      <p className="meta">
        LLM calls made for this organization and what they cost, as estimated from the model's price. Limits are set by
        the hosting company.
      </p>

      <section aria-label="This month">
        <h2>This month ({budget.month})</h2>
        <p>
          <strong>{usd(budget.spent_usd)}</strong>
          {budget.budget_usd !== null && <> of {usd(budget.budget_usd)}</>}
        </p>
        <p className={budget.status === 'warning' || budget.status === 'stopped' ? 'error' : 'meta'} role={budget.status === 'stopped' ? 'alert' : undefined}>
          {BUDGET_TEXT[budget.status]}
        </p>
        {budget.own_key && (
          <p className="meta">
            Calls to the model provider use your own key: the budget counts the hosting company's key only, so it no
            longer limits them.
          </p>
        )}
        <dl className="usage-limits">
          <dt>Monthly LLM budget</dt><dd>{limitText(limits, 'llm_monthly_budget_usd')}</dd>
          <dt>LLM requests per minute</dt><dd>{limitText(limits, 'llm_requests_per_minute')}</dd>
          <dt>Concurrent jobs</dt>
          <dd>
            {limitText(limits, 'max_concurrent_jobs')} ({usage.jobs_running} running, {usage.jobs_queued} queued)
          </dd>
        </dl>
      </section>

      <section aria-label="By month">
        <h2>By month</h2>
        {usage.months.length === 0 ? (
          <p className="meta">No LLM calls recorded yet.</p>
        ) : (
          <table className="data-table">
            <thead>
              <tr><th>Month</th><th>Calls</th><th>Input tokens</th><th>Output tokens</th><th>Cost</th><th /></tr>
            </thead>
            <tbody>
              {usage.months.map((month) => (
                <Fragment key={month.month}>
                  <tr>
                    <td>{month.month}</td>
                    <td>{month.calls}</td>
                    <td>{tokens(month.input_tokens)}</td>
                    <td>{tokens(month.output_tokens)}</td>
                    <td>
                      {usd(month.cost_usd)}
                      {month.own_key_cost_usd > 0 && (
                        <span className="meta"> ({usd(month.own_key_cost_usd)} on your own key)</span>
                      )}
                      {month.calls_without_cost > 0 && (
                        <span className="meta" title="Calls to a model with no known price add nothing to the cost">
                          {' '}+ {month.calls_without_cost} call(s) without a price
                        </span>
                      )}
                    </td>
                    <td>
                      <button type="button" className="button-secondary" onClick={() => setOpen(open === month.month ? null : month.month)}>
                        {open === month.month ? 'Hide' : 'By operation'}
                      </button>
                    </td>
                  </tr>
                  {open === month.month &&
                    month.operations.map((op) => (
                      <tr key={`${month.month}-${op.operation}-${op.model}`} className="usage-operation">
                        <td />
                        <td>{op.calls}</td>
                        <td colSpan={2}>
                          {op.operation}{' '}
                          <span className="meta">
                            ({op.model}, {tokens(op.total_tokens)} tokens{op.key_source === 'tenant' ? ', your own key' : ''})
                          </span>
                        </td>
                        <td>{usd(op.cost_usd)}</td>
                        <td />
                      </tr>
                    ))}
                </Fragment>
              ))}
            </tbody>
          </table>
        )}
      </section>
    </div>
  )
}
