import { useEffect, useState } from 'react'
import { ApiError, listRegistries, pushToRegistry } from '../api/client'
import type { RegistryEntry, RegistryStatus } from '../api/types'

// Epic 13's connectivity demonstrated end-to-end from an Epic 12 artifact
// -- deliberately minimal (no status/lifecycle tracking on the artifact
// itself, no republish-as-new-version). The polished, status-tracked,
// access-controlled "Publish" action belongs to Epic 15, built on top of
// the same /api/registries/{name}/push endpoint this uses directly.
export default function RegistryPushAction({
  agentName,
  definition,
  tags,
  processId,
  nodeIds,
  canPush,
}: {
  agentName: string
  definition: Record<string, unknown>
  tags: string[]
  processId: string
  nodeIds: string[]
  canPush: boolean
}) {
  const [registries, setRegistries] = useState<RegistryStatus[]>([])
  const [selected, setSelected] = useState('')
  const [pushing, setPushing] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [pushed, setPushed] = useState<RegistryEntry | null>(null)

  useEffect(() => {
    listRegistries()
      .then((list) => {
        setRegistries(list)
        setSelected((current) => current || list[0]?.name || '')
      })
      .catch(() => {
        // Best-effort -- if this fails the select stays empty and the
        // button stays disabled, which is a reasonable degraded state.
      })
  }, [])

  useEffect(() => {
    setPushed(null)
    setError(null)
  }, [agentName])

  async function handlePush() {
    if (!selected) return
    setPushing(true)
    setError(null)
    try {
      const entry = await pushToRegistry(selected, {
        agent_name: agentName,
        definition,
        tags,
        source_process_id: processId,
        source_node_ids: nodeIds,
      })
      setPushed(entry)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to push to registry')
    } finally {
      setPushing(false)
    }
  }

  if (registries.length === 0) return null

  return (
    <div className="registry-push-action">
      <label>
        Registry
        <select value={selected} onChange={(event) => setSelected(event.target.value)}>
          {registries.map((registry) => (
            <option key={registry.name} value={registry.name} disabled={!registry.reachable}>
              {registry.name}
              {registry.reachable ? '' : ' (unreachable)'}
            </option>
          ))}
        </select>
      </label>
      <button type="button" onClick={handlePush} disabled={pushing || !selected || !canPush} title={!canPush ? 'Editor access required' : undefined}>
        {pushing ? 'Pushing...' : 'Push to registry'}
      </button>
      {error && <p className="error">{error}</p>}
      {pushed && <p className="info">Pushed to &ldquo;{pushed.registry_name}&rdquo;.</p>}
    </div>
  )
}
