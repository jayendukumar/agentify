import { useEffect, useState } from 'react'
import { ApiError, linkSystem, listContextEntities, listSystemResolutions } from '../api/client'
import type { ContextEntity, SystemResolution } from '../api/types'

const STATUS_LABELS: Record<SystemResolution['status'], string> = {
  linked: 'Linked',
  matched: 'Matched automatically',
  ambiguous: 'Needs a choice',
  unmatched: 'Not in the catalog',
}

/** Epic 29, US29.3: every system name this process uses, and the catalog
 * application it means. Exact matches link themselves; anything fuzzy is
 * a person's choice. */
export default function SystemResolutionPanel({ processId, canEdit }: { processId: string; canEdit: boolean }) {
  const [rows, setRows] = useState<SystemResolution[]>([])
  const [apps, setApps] = useState<ContextEntity[]>([])
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([listSystemResolutions(processId), listContextEntities('application')])
      .then(([resolutions, appList]) => {
        setRows(resolutions)
        setApps(appList)
      })
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Could not load systems'))
  }, [processId])

  async function choose(systemName: string, value: string) {
    setError(null)
    try {
      setRows(await linkSystem(processId, systemName, value === '__none__' ? null : value))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not save that link')
    }
  }

  if (rows.length === 0 && !error) return null
  const needsChoice = rows.filter((r) => r.status === 'ambiguous').length

  return (
    <section aria-labelledby="systems-heading" className="system-resolution">
      <h2 id="systems-heading">Systems</h2>
      <p className="meta">
        The systems this process mentions, matched to your application catalog.
        {needsChoice > 0 && ` ${needsChoice} need${needsChoice === 1 ? 's' : ''} you to pick which application is meant.`}
      </p>
      {error && <p className="error" role="alert">{error}</p>}
      <table>
        <thead><tr><th>Name used</th><th>Application</th><th>Status</th><th>Used in</th></tr></thead>
        <tbody>
          {rows.map((row) => (
            <tr key={row.system_name}>
              <td>{row.system_name}</td>
              <td>
                {canEdit ? (
                  <select
                    aria-label={`Application for ${row.system_name}`}
                    value={row.application_id ?? (row.status === 'unmatched' ? '__none__' : '')}
                    onChange={(e) => choose(row.system_name, e.target.value)}
                  >
                    <option value="" disabled>Choose...</option>
                    {row.candidates.length > 0 && (
                      <optgroup label="Likely matches">
                        {row.candidates.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                      </optgroup>
                    )}
                    <optgroup label="All applications">
                      {apps.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
                    </optgroup>
                    <option value="__none__">Not in the catalog</option>
                  </select>
                ) : (
                  row.application_name ?? '--'
                )}
              </td>
              <td><span className={`badge ${row.status === 'ambiguous' ? 'confidence-medium' : row.status === 'unmatched' ? '' : 'status-done'}`}>{STATUS_LABELS[row.status]}</span></td>
              <td className="meta">{row.used_in.join('; ')}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </section>
  )
}
