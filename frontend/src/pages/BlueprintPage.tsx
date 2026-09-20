import { useCallback, useEffect, useMemo, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import {
  ApiError,
  exportBlueprint,
  generateAgentArtifact,
  generateBlueprint,
  getBlueprint,
  getProcess,
  getVersion,
  listAgentArtifacts,
  overrideBlueprintNode,
} from '../api/client'
import type { AgentArtifact, BlueprintOverlay, BlueprintVerdict, ProcessDetail } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import AgentCardsPanel from '../components/AgentCardsPanel'
import BlueprintCanvas from '../components/BlueprintCanvas'
import BlueprintDetailPanel from '../components/BlueprintDetailPanel'
import DigitalTwinPreview from '../components/DigitalTwinPreview'
import { computeAgentGroups } from '../lib/blueprintLabels'
import { downloadText } from '../lib/exportPng'

function markerClass(verdict: BlueprintVerdict): string {
  return `blueprint-node-${verdict.replace(/_/g, '-')}`
}

type Tab = 'blueprint' | 'agents' | 'twin'

export default function BlueprintPage() {
  const { user } = useAuth()
  const isEditor = user?.role === 'editor'
  const { processId } = useParams<{ processId: string }>()

  const [process, setProcess] = useState<ProcessDetail | null>(null)
  const [overlay, setOverlay] = useState<BlueprintOverlay | null>(null)
  const [versionXml, setVersionXml] = useState<string | null>(null)
  const [labelsById, setLabelsById] = useState<Record<string, string>>({})
  const [selectedNodeId, setSelectedNodeId] = useState<string | null>(null)
  const [artifacts, setArtifacts] = useState<AgentArtifact[]>([])
  const [activeTab, setActiveTab] = useState<Tab>('blueprint')

  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [generating, setGenerating] = useState(false)
  const [generateError, setGenerateError] = useState<string | null>(null)
  const [overriding, setOverriding] = useState(false)
  const [exportError, setExportError] = useState<string | null>(null)
  const [generatingAgentNodeId, setGeneratingAgentNodeId] = useState<string | null>(null)

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
          const artifactList = await listAgentArtifacts(processId)
          if (!cancelled) setArtifacts(artifactList)
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
      // Existing agent artifacts aren't deleted by a blueprint regenerate,
      // but their staleness (computed server-side, US12.4) may have
      // changed -- refetch rather than assume.
      setArtifacts(await listAgentArtifacts(processId))
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
      setArtifacts(await listAgentArtifacts(processId))
    } finally {
      setOverriding(false)
    }
  }

  async function handleGenerateAgent(nodeId: string) {
    if (!processId || !nodeId) return
    setGeneratingAgentNodeId(nodeId)
    try {
      await generateAgentArtifact(processId, nodeId)
      setArtifacts(await listAgentArtifacts(processId))
    } finally {
      setGeneratingAgentNodeId(null)
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

  const agentGroups = useMemo(() => (overlay ? computeAgentGroups(overlay, artifacts) : []), [overlay, artifacts])

  const stats = useMemo(() => {
    if (!overlay) return null
    const total = overlay.nodes.length
    const automatable = overlay.nodes.filter((n) => n.verdict === 'automatable').length
    const partial = overlay.nodes.filter((n) => n.verdict === 'partial').length
    const notAutomatable = overlay.nodes.filter((n) => n.verdict === 'not_automatable')

    return {
      total,
      automatable,
      partial,
      notAutomatable,
      agentCount: agentGroups.length,
      automatablePercent: total === 0 ? 0 : Math.round(((automatable + partial) / total) * 100),
    }
  }, [overlay, agentGroups])

  const selectedNode = overlay?.nodes.find((n) => n.node_id === selectedNodeId) ?? null
  const selectedArtifact = artifacts.find((a) => selectedNodeId && a.node_ids.includes(selectedNodeId)) ?? null

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

      <div className="tab-bar" role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'blueprint'}
          className={`tab-button${activeTab === 'blueprint' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('blueprint')}
        >
          Blueprint
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'agents'}
          className={`tab-button${activeTab === 'agents' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('agents')}
        >
          Agents ({agentGroups.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'twin'}
          className={`tab-button${activeTab === 'twin' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('twin')}
        >
          Digital Twin Preview
        </button>
      </div>

      {/* The canvas stays mounted across tabs (just hidden) rather than being
          unmounted/remounted per tab -- re-initializing bpmn-js is expensive,
          and onDiagramReady's labelsById is needed by the other two tabs too. */}
      {/* Inline style (not a CSS class) so it hides reliably even where a
          stylesheet isn't loaded, e.g. component tests. */}
      <div className="diagram-body" style={activeTab === 'blueprint' ? undefined : { display: 'none' }}>
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
          artifact={selectedArtifact}
          onGenerateAgent={() => handleGenerateAgent(selectedNodeId ?? '')}
          generatingAgent={generatingAgentNodeId !== null && generatingAgentNodeId === selectedNodeId}
          canGenerateAgent={isEditor}
        />
      </div>

      {activeTab === 'agents' && (
        <AgentCardsPanel
          groups={agentGroups}
          labelsById={labelsById}
          selectedNodeId={selectedNodeId}
          onSelect={setSelectedNodeId}
          onGenerateAgent={handleGenerateAgent}
          generatingNodeId={generatingAgentNodeId}
          canGenerateAgent={isEditor}
        />
      )}

      {activeTab === 'twin' && overlay && (
        <DigitalTwinPreview overlay={overlay} groups={agentGroups} labelsById={labelsById} />
      )}
    </div>
  )
}
