import { useState } from 'react'
import { ApiError, applyStarterPack } from '../../api/client'
import type { StarterPackSummary } from '../../api/types'

interface Props {
  packs: StarterPackSummary[]
  canEdit: boolean
  onApplied: () => void
}

/** US27.7: curated starter regulatory packs. */
export default function StarterPacksPanel({ packs, canEdit, onApplied }: Props) {
  const [busy, setBusy] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)

  async function handleApply(pack: StarterPackSummary) {
    setBusy(pack.id)
    setMessage(null)
    try {
      const result = await applyStarterPack(pack.id)
      setMessage({
        kind: 'info',
        text: `${pack.title}: ${result.created} added, ${result.updated} updated, ${result.unchanged} already up to date.`,
      })
      onApplied()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not add the pack' })
    } finally {
      setBusy(null)
    }
  }

  return (
    <section aria-labelledby="packs-heading">
      <h2 id="packs-heading">Starter rule packs</h2>
      <p className="meta">
        Plain-language obligations and controls for common regulations, so you don't have to upload the regulations
        themselves. Each pack is versioned; adding a newer version updates the same rules and records the change.
      </p>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}
      <ul className="registry-entry-list">
        {packs.map((pack) => {
          const upToDate = pack.applied_version === pack.version
          return (
            <li key={pack.id} className="registry-entry-card">
              <div className="registry-entry-header">
                <strong>{pack.title}</strong>
                <span className="meta">v{pack.version}</span>
                {pack.suggested && <span className="badge confidence-high">Suggested for you</span>}
                {pack.applied_version && (
                  <span className={`badge ${upToDate ? 'status-done' : 'confidence-medium'}`}>
                    {upToDate ? 'Added' : `v${pack.applied_version} added -- update available`}
                  </span>
                )}
              </div>
              <p>{pack.description}</p>
              <p className="meta">
                {pack.obligation_count} obligations, {pack.control_count} controls · Applies in {pack.jurisdictions.join(', ')}
                {pack.industries.length > 0 && ` · For ${pack.industries.join(', ')}`}
              </p>
              <p className="meta twin-disclaimer">{pack.disclaimer}</p>
              <div className="registry-entry-actions">
                <button
                  type="button"
                  className={upToDate ? 'button-secondary' : 'button-primary'}
                  disabled={!canEdit || busy !== null || upToDate}
                  onClick={() => handleApply(pack)}
                >
                  {busy === pack.id ? 'Adding...' : upToDate ? 'Up to date' : pack.applied_version ? 'Update' : 'Add pack'}
                </button>
              </div>
            </li>
          )
        })}
      </ul>
    </section>
  )
}
