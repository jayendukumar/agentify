import { useState } from 'react'
import { ApiError, getContextPack } from '../../api/client'
import type { Citation, ContextPack } from '../../api/types'
import { KIND_LABELS } from '../../lib/contextLabels'

/** Epic 28, US28.3: the rules and policy passages an output relied on. */
export function CitationList({ citations, heading }: { citations: Citation[]; heading: string }) {
  if (citations.length === 0) return null
  return (
    <div className="org-citations">
      <strong>{heading}</strong>
      {citations.map((citation, index) => (
        <blockquote key={index} className="org-citation">
          {citation.clause_ref && <span className="meta">{citation.clause_ref}</span>} {citation.quote}
        </blockquote>
      ))}
    </div>
  )
}

/** Epic 28, US28.9: the exact Context Pack an output was produced with. */
export function ContextPackInspector({ packId, label = 'Organization context used' }: { packId: string; label?: string }) {
  const [open, setOpen] = useState(false)
  const [pack, setPack] = useState<ContextPack | null>(null)
  const [error, setError] = useState<string | null>(null)

  async function toggle() {
    const next = !open
    setOpen(next)
    if (next && !pack) {
      try {
        setPack(await getContextPack(packId))
      } catch (err) {
        setError(err instanceof ApiError ? err.message : 'Could not load the context pack')
      }
    }
  }

  return (
    <div className="org-pack-inspector">
      <button type="button" className="link-button" onClick={toggle} aria-expanded={open}>
        {open ? 'Hide' : 'Show'} {label.toLowerCase()}
      </button>
      {open && error && <p className="error" role="alert">{error}</p>}
      {open && pack && (
        <div className="org-pack-body">
          <p className="meta">
            {pack.entries.length} rules and facts, {pack.passages.length} policy passages · about {pack.token_estimate} of{' '}
            {pack.budget} tokens{pack.truncated ? ` · ${pack.truncated} lower-priority items left out to fit` : ''}
          </p>
          {pack.stale_entries.length > 0 && (
            <p className="error" role="status">
              Changed since: {pack.stale_entries.map((s) => `${s.name} (v${s.pinned_version} -> ${s.current_version ?? 'removed'}${s.current_status && s.current_status !== 'confirmed' ? `, ${s.current_status}` : ''})`).join('; ')}
            </p>
          )}
          {pack.entries.length === 0 && pack.passages.length === 0 && (
            <p className="meta">No organization context applied -- set the process's business unit and countries to use it.</p>
          )}
          <ul>
            {pack.entries.map((entry) => (
              <li key={entry.label}>
                <span className="badge">{entry.label}</span> <span className="meta">{KIND_LABELS[entry.kind]?.singular ?? entry.kind} v{entry.version}</span> {entry.text}
              </li>
            ))}
            {pack.passages.map((passage) => (
              <li key={passage.label}>
                <span className="badge">{passage.label}</span> <span className="meta">{passage.clause_ref} (policy v{passage.policy_version})</span> {passage.text}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}
