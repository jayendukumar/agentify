import { useEffect, useState } from 'react'
import { ApiError, listRegistries, searchRegistries } from '../api/client'
import type { RegistryEntry, RegistryStatus } from '../api/types'
import { downloadText } from '../lib/exportPng'

export default function RegistriesPage() {
  const [registries, setRegistries] = useState<RegistryStatus[]>([])
  const [query, setQuery] = useState('')
  const [entries, setEntries] = useState<RegistryEntry[]>([])
  const [registryErrors, setRegistryErrors] = useState<Record<string, string>>({})
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [searching, setSearching] = useState(false)
  const [searchError, setSearchError] = useState<string | null>(null)
  const [searched, setSearched] = useState(false)

  async function runSearch(q: string) {
    setSearching(true)
    setSearchError(null)
    try {
      const result = await searchRegistries(q)
      setEntries(result.entries)
      setRegistryErrors(result.registry_errors)
      setSearched(true)
    } catch (err) {
      setSearchError(err instanceof ApiError ? err.message : 'Failed to search registries')
    } finally {
      setSearching(false)
    }
  }

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setLoadError(null)

    Promise.all([listRegistries(), searchRegistries('')])
      .then(([registryList, result]) => {
        if (cancelled) return
        setRegistries(registryList)
        setEntries(result.entries)
        setRegistryErrors(result.registry_errors)
        setSearched(true)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load registries')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [])

  function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    void runSearch(query)
  }

  function handleDownload(entry: RegistryEntry) {
    downloadText(
      JSON.stringify(entry.definition, null, 2),
      `${entry.agent_name.replace(/\s+/g, '_').toLowerCase()}.json`,
      'application/json',
    )
  }

  if (loading) return <p className="loading-state" role="status">Loading registries...</p>

  return (
    <div className="page">
      <h1>Agent Registries</h1>
      <p className="meta">
        Browse and search agents already registered across connected registries, so you can check for an existing
        equivalent agent before generating and publishing a duplicate for the same process step.
      </p>

      {loadError && <div><p className="error" role="alert">{loadError}</p><button type="button" onClick={() => window.location.reload()}>Try again</button></div>}

      <div className="registry-status-list">
        {registries.map((registry) => (
          <div key={registry.name} className="registry-status-card">
            <span className="registry-status-name">{registry.name}</span>
            <span className="meta">{registry.type}</span>
            <span className={`badge ${registry.reachable ? 'status-done' : 'status-failed'}`}>
              {registry.reachable ? 'reachable' : 'unreachable'}
            </span>
            <span className={`badge ${registry.authenticated ? 'status-done' : 'status-failed'}`}>
              {registry.authenticated ? 'authenticated' : 'auth failed'}
            </span>
            {registry.message && <span className="meta">{registry.message}</span>}
          </div>
        ))}
      </div>

      <form className="create-form" onSubmit={handleSubmit}>
        <label>
          Search agents
        <input
          type="text"
          placeholder="Search by agent name or tag..."
          value={query}
          onChange={(event) => setQuery(event.target.value)}
        />
        </label>
        <button type="submit" disabled={searching}>
          {searching ? 'Searching...' : 'Search'}
        </button>
      </form>

      {searchError && <p className="error" role="alert">{searchError}</p>}
      {searching && <p role="status" className="meta">Searching connected registries...</p>}

      {Object.entries(registryErrors).map(([name, message]) => (
        <p className="error" role="alert" key={name}>
          Registry &ldquo;{name}&rdquo; could not be searched: {message}
        </p>
      ))}

      {searched && entries.length === 0 && !searchError && !loadError && <p className="empty-state" role="status">No registered agents found. Try another name or tag, or publish an agent from a process blueprint.</p>}

      <ul className="registry-entry-list">
        {entries.map((entry) => (
          <li key={entry.id} className="registry-entry-card">
            <div className="registry-entry-header">
              <span className="element-label">{entry.agent_name}</span>
              <span className="badge">{entry.registry_name}</span>
            </div>
            {entry.tags.length > 0 && (
              <div className="registry-entry-tags">
                {entry.tags.map((tag) => (
                  <span className="badge" key={tag}>
                    {tag}
                  </span>
                ))}
              </div>
            )}
            <span className="meta">
              Pushed {new Date(entry.pushed_at).toLocaleString()}
              {entry.pushed_by_name ? ` by ${entry.pushed_by_name}` : ''}
            </span>
            <div className="registry-entry-actions">
              <button type="button" aria-expanded={expandedId === entry.id} aria-controls={`definition-${entry.id}`} onClick={() => setExpandedId(expandedId === entry.id ? null : entry.id)}>
                {expandedId === entry.id ? 'Hide definition' : 'View definition'}
              </button>
              <button type="button" className="button-secondary" onClick={() => handleDownload(entry)}>
                Download
              </button>
            </div>
            {expandedId === entry.id && (
              <pre id={`definition-${entry.id}`} className="agent-system-prompt" tabIndex={0} aria-label={`${entry.agent_name} definition`}>{JSON.stringify(entry.definition, null, 2)}</pre>
            )}
          </li>
        ))}
      </ul>
    </div>
  )
}
