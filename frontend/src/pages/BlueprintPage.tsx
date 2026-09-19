import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  exportBlueprint,
  generateBlueprint,
  getBlueprint,
  getProcess,
  getVersion,
  overrideBlueprintNode,
} from '../api/client'
import type { BlueprintOverlay, BlueprintVerdict, ProcessDetail } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import BlueprintCanvas from '../components/BlueprintCanvas'
import BlueprintDetailPanel from '../components/BlueprintDetailPanel'
import { downloadText } from '../lib/exportPng'

function markerClass(verdict: BlueprintVerdict): string {
  return `blueprint-node-${verdict.replace(/_/g, '-')}`
}

export default function BlueprintPage() {
  const { user } = useAuth()
  const isEditor = user?.role === 'editor'
  const { processId } = useParams<{ processId: string }>()

  const [process, setProcess] = useState<ProcessDetail | null>(null)
  const [overlay, setOverlay] = useState<BlueprintOverlay | null>(null)
  const [versionXml, setVersionXml] = useState<string | null>(null)
  const [labelsById, setLabelsById] = useState<Record<string, string>>({})
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [overriding, setOverriding] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)

  const loadVersionXml = useCallback(
    async (versionId: string) => {
      if (!processId) return
      const version = await getVersion(processId, versionId)
      setVersionXml(version.xml)
    },
    [processId],
  )

  useEffect(() => {
    if (!processId) return
    let cancelled = false

    setLoading(true)
    setLoadError(null)
    setSelectedNodeId(null)

    Promise.all([getProcess(processId), getBlueprint(processId)])
      .then(async ([processDetail, blueprintOverlay]) => {
        if (cancelled) return
        setProcess(processDetail)
        setOverlay(blueprintOverlay)
        if (blueprintOverlay) {
          await loadVersionXml(blueprintOverlay.baseline_version_id)
        }
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load blueprint')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [processId, loadVersionXml])

  async function handleGenerate() {
    if (!processId) return
    setGenerating(true)
    setGenerateError(null)
    try {
      const result = await generateBlueprint(processId)
      setOverlay(result)
      setSelectedNodeId(null)
      await loadVersionXml(result.baseline_version_id)
    } catch (err) {
      setGenerateError(err instanceof ApiError ? err.message : 'Failed to generate blueprint')
    } finally {
      setGenerating(false)
    }
  }

  async function handleOverride(verdict: BlueprintVerdict, justification: string) {
    if (!processId || !selectedNodeId) return
    setOverriding(true)
    try {
      const result = await overrideBlueprintNode(processId, selectedNodeId, verdict, justification)
      setOverlay(result)
    } finally {
      setOverriding(false)
    }
  }

  async function handleExport() {
    if (!processId || !process) return
    setExportError(null)
    try {
      const markdown = await exportBlueprint(processId)
      downloadText(markdown, `${process.name}-blueprint.md`, 'text/markdown')
    } catch (err) {
      setExportError(err instanceof ApiError ? err.message : 'Failed to export blueprint')
    }
  }

  const markers = useMemo(() => {
    if (!overlay) return {}
    const map: Record<string, string> = {}
    for (const node of overlay.nodes) {
      map[node.node_id] = markerClass(node.verdict)
    }
    return map
  }, [overlay])

  const stats = useMemo(() => {
    if (!overlay) return null
    const total = overlay.nodes.length
    const automatable = overlay.nodes.filter((n) => n.verdict === 'automatable').length
    const partial = overlay.nodes.filter((n) => n.verdict === 'partial').length
    const notAutomatable = overlay.nodes.filter((n) => n.verdict === 'not_automatable')

    // Consolidated nodes share the same consolidated_from_nodes group -- dedupe
    // on that (sorted) group rather than counting one agent per node.
    const agentKeys = new Set(
      overlay.nodes
        .filter((n) => n.agent_spec)
        .map((n) =>
          n.agent_spec!.consolidated_from_nodes.length > 0
            ? [...n.agent_spec!.consolidated_from_nodes].sort().join('|')
            : n.node_id,
        ),
    )

    return {
      total,
      automatable,
      partial,
      notAutomatable,
      agentCount: agentKeys.size,
      automatablePercent: total === 0 ? 0 : Math.round(((automatable + partial) / total) * 100),
    }
  }, [overlay])

  const selectedNode = overlay?.nodes.find((n) => n.node_id === selectedNodeId) ?? null

  if (!processId) return <p>Missing process id.</p>

  if (loadError) {
    return (
      <div className="page">
        <p>
          <Link to="/">&larr; All processes</Link>
        </p>
        <p className="error">{loadError}</p>
      </div>
    )
  }

  if (loading || !process) return <p>Loading...</p>

  if (!overlay) {
    return (
      <div className="page">
        <p>
          <Link to={`/processes/${processId}/diagram`}>&larr; Back to diagram</Link>
        </p>
        <h2>Agentic Blueprint</h2>
        {process.finalized_version_count === 0 ? (
          <p>No finalized version yet -- finalize a diagram baseline first.</p>
        ) : (
          <>
            <p>No blueprint generated yet for this process.</p>
            <button
              type="button"
              onClick={handleGenerate}
              disabled={generating || !isEditor}
              title={!isEditor ? 'Editor access required' : undefined}
            >
              {generating ? 'Generating...' : 'Generate Blueprint'}
            </button>
          </>
        )}
        {generateError && <p className="error">{generateError}</p>}
      </div>
    )
  }

  return (
    <div className="diagram-page">
      <div className="diagram-toolbar">
        <Link to={`/processes/${processId}/diagram`}>&larr; {process.name}</Link>
        <h2 className="blueprint-title">Agentic Blueprint</h2>

        <button
          type="button"
          onClick={handleGenerate}
          disabled={generating || !versionXml || !isEditor}
          title={!isEditor ? 'Editor access required' : undefined}
        >
          {generating ? 'Regenerating...' : 'Regenerate'}
        </button>
        <button type="button" onClick={handleExport}>
          Export Markdown
        </button>
      </div>

      {generateError && <p className="error">{generateError}</p>}
      {exportError && <p className="error">{exportError}</p>}

      {stats && (
        <div className="blueprint-summary">
          <div className="blueprint-stat">
            <span className="blueprint-stat-value">{stats.automatablePercent}%</span>
            <span className="meta">of steps automatable or partially automatable</span>
          </div>
          <div className="blueprint-stat">
            <span className="blueprint-stat-value">{stats.agentCount}</span>
            <span className="meta">agents identified</span>
          </div>
          <div className="blueprint-stat">
            <span className="blueprint-stat-value">{stats.total}</span>
            <span className="meta">
              steps evaluated ({stats.automatable} automatable, {stats.partial} partial, {stats.notAutomatable.length}{' '}
              not automatable)
            </span>
          </div>
          {stats.notAutomatable.length > 0 && (
            <details className="blueprint-not-automatable">
              <summary>Not automatable steps</summary>
              <ul>
                {stats.notAutomatable.map((node) => (
                  <li key={node.node_id}>
                    <strong>{labelsById[node.node_id] ?? node.node_id}:</strong>{' '}
                    {node.not_automatable_reason ?? node.rationale}
                  </li>
                ))}
              </ul>
            </details>
          )}
        </div>
      )}

      <div className="diagram-body">
        {versionXml && (
          <BlueprintCanvas
            xml={versionXml}
            markers={markers}
            onSelectionChange={setSelectedNodeId}
            onDiagramReady={setLabelsById}
          />
        )}
        <BlueprintDetailPanel
          node={selectedNode}
          label={selectedNodeId ? (labelsById[selectedNodeId] ?? selectedNodeId) : null}
          labelsById={labelsById}
          onOverride={handleOverride}
          overriding={overriding}
          canOverride={isEditor}
        />
      </div>
    </div>
  )
}
