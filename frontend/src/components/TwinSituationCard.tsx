import type { ComplianceAssertion, CoverageTarget, Situation } from '../api/types'
import { REVIEWER_LABELS, SYSTEM_BEHAVIOUR_LABELS } from '../lib/twinSituations'

// Epic 24, US24.1: one proposed test situation as a Given/When/Then story
// card -- what a business reader reviews before anything is saved.
// Epic 30, US30.5: what each rule check asserts, in plain language.
const COMPLIANCE_LABELS: Record<ComplianceAssertion['kind'], (a: ComplianceAssertion) => string> = {
  escalated: (a) => `escalated to ${a.role ?? 'a person'}`,
  blocked: (a) => (a.system ? `nothing done in ${a.system}` : 'the action is blocked'),
  reviewed: (a) => `a person reviews before ${a.system ? `anything happens in ${a.system}` : 'acting'}`,
  logged: (a) => `decision logged with ${a.field ?? 'an explanation'}`,
}

export default function TwinSituationCard({
  situation,
  targets,
  state,
  onKeep,
  onSkip,
}: {
  situation: Situation
  targets: CoverageTarget[]
  state: 'proposed' | 'kept' | 'skipped'
  onKeep?: () => void
  onSkip?: () => void
}) {
  const { story } = situation
  const labelFor = (id: string) => targets.find((t) => t.id === id)?.label ?? id
  const unusualSystems = Object.entries(story.system_behaviour).filter(([, b]) => b !== 'normal')

  return (
    <article
      className={`situation-card situation-card-${state}`}
      data-testid="situation-card"
      aria-label={story.title}
    >
      <header className="situation-card-header">
        <h4 className="situation-card-title">{story.title}</h4>
        {story.source === 'described' && <span className="badge">Your description</span>}
      </header>
      <dl className="situation-story">
        {story.given && (
          <>
            <dt>Given</dt>
            <dd>{story.given}</dd>
          </>
        )}
        {story.when && (
          <>
            <dt>When</dt>
            <dd>{story.when}</dd>
          </>
        )}
        {story.then && (
          <>
            <dt>Then</dt>
            <dd>{story.then}</dd>
          </>
        )}
        {(story.expect.compliance ?? []).length > 0 && (
          <>
            <dt>Rule check</dt>
            <dd>
              {(story.expect.compliance ?? []).map((a) => COMPLIANCE_LABELS[a.kind](a)).join('; ')}
            </dd>
          </>
        )}
      </dl>
      {(unusualSystems.length > 0 || story.reviewer.behaviour !== 'none') && (
        <ul className="situation-conditions" aria-label="Conditions">
          {unusualSystems.map(([system, behaviour]) => (
            <li key={system}>
              <strong>{system}</strong> {SYSTEM_BEHAVIOUR_LABELS[behaviour].toLowerCase()}
            </li>
          ))}
          {story.reviewer.behaviour !== 'none' && (
            <li>
              <strong>{story.reviewer.persona_name ?? 'Reviewer'}</strong>{' '}
              {story.reviewer.behaviour === 'threshold'
                ? `rejects when ${story.reviewer.field} > ${story.reviewer.threshold}`
                : REVIEWER_LABELS[story.reviewer.behaviour].toLowerCase()}
            </li>
          )}
        </ul>
      )}
      {story.covers.length > 0 && (
        <p className="situation-covers">
          <span className="meta">Tests:</span>{' '}
          {story.covers.map((id) => (
            <span key={id} className="badge coverage-chip">
              {labelFor(id)}
            </span>
          ))}
        </p>
      )}
      {(onKeep || onSkip) && (
        <div className="situation-card-actions">
          {onKeep && (
            <button type="button" className={state === 'kept' ? 'button-primary' : undefined} onClick={onKeep} aria-pressed={state === 'kept'}>
              {state === 'kept' ? '✓ Kept' : 'Keep'}
            </button>
          )}
          {onSkip && (
            <button type="button" className="button-secondary" onClick={onSkip} aria-pressed={state === 'skipped'}>
              {state === 'skipped' ? 'Skipped' : 'Skip'}
            </button>
          )}
        </div>
      )}
    </article>
  )
}
