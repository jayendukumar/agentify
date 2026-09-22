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
import DigitalTwinPanel from '../components/DigitalTwinPanel'
import DigitalTwinPreview from '../components/DigitalTwinPreview'
import { computeAgentGroups } from '../lib/blueprintLabels'
import { downloadText } from '../lib/exportPng'
import { handleTabKeyDown } from '../lib/tabKeyboard'

function markerClass(verdict: BlueprintVerdict): string {
  return `blueprint-node-${verdict.replace(/_/g, '-')}`
}

type Tab = 'blueprint' | 'agents' | 'twin' | 'twin-runs'

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
    if (overlay && !window.confirm('Regenerate the blueprint? This replaces the current recommendations and manual overrides. Export Markdown first if you need a copy.')) return
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
        <p className="error" role="alert">{loadError}</p>
        <button type="button" onClick={() => window.location.reload()}>Try again</button>
      </div>
    )
  }

  if (loading || !process) return <p className="loading-state" role="status">Loading blueprint...</p>

  if (!overlay) {
    return (
      <div className="page">
        <p>
          <Link to={`/processes/${processId}/diagram`}>&larr; Back to diagram</Link>
        </p>
        <h1>Agentic Blueprint</h1>
        {process.finalized_version_count === 0 ? (
          <p>No finalized version yet -- finalize a diagram baseline first.</p>
        ) : (
          <>
            <p>No blueprint generated yet for this process.</p>
            <button
              type="button"
              onClick={handleGenerate}
              className="button-primary"
              disabled={generating || !isEditor}
              title={!isEditor ? 'Editor access required' : undefined}
            >
              {generating ? 'Generating...' : 'Generate Blueprint'}
            </button>
          </>
        )}
        {generateError && <p className="error" role="alert">{generateError}</p>}
      </div>
    )
  }

  return (
    <div className="diagram-page">
      <div className="diagram-heading">
        <h1>Agentic Blueprint</h1>
        <p className="meta">{process.name} · Review automation recommendations, generated agents and simulation results.</p>
      </div>
      <div className="diagram-toolbar">
        <Link to={`/processes/${processId}/diagram`}>&larr; {process.name}</Link>

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

      {generateError && <p className="error" role="alert">{generateError}</p>}
      {exportError && <p className="error" role="alert">{exportError}</p>}

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

      <div className="tab-bar" role="tablist" aria-label="Blueprint views" onKeyDown={handleTabKeyDown}>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'blueprint'}
          id="tab-blueprint" aria-controls="panel-blueprint" tabIndex={activeTab === 'blueprint' ? 0 : -1}
          className={`tab-button${activeTab === 'blueprint' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('blueprint')}
        >
          Blueprint
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'agents'}
          id="tab-agents" aria-controls="panel-agents" tabIndex={activeTab === 'agents' ? 0 : -1}
          className={`tab-button${activeTab === 'agents' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('agents')}
        >
          Agents ({agentGroups.length})
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'twin'}
          id="tab-twin" aria-controls="panel-twin" tabIndex={activeTab === 'twin' ? 0 : -1}
          className={`tab-button${activeTab === 'twin' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('twin')}
        >
          Digital Twin Preview
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={activeTab === 'twin-runs'}
          id="tab-twin-runs" aria-controls="panel-twin-runs" tabIndex={activeTab === 'twin-runs' ? 0 : -1}
          className={`tab-button${activeTab === 'twin-runs' ? ' tab-button-active' : ''}`}
          onClick={() => setActiveTab('twin-runs')}
        >
          Digital Twin Simulation
        </button>
      </div>

      {/* The canvas stays mounted across tabs (just hidden) rather than being
          unmounted/remounted per tab -- re-initializing bpmn-js is expensive,
          and onDiagramReady's labelsById is needed by the other two tabs too. */}
      {/* Inline style (not a CSS class) so it hides reliably even where a
          stylesheet isn't loaded, e.g. component tests. */}
      <div id="panel-blueprint" role="tabpanel" aria-labelledby="tab-blueprint" tabIndex={0} style={activeTab === 'blueprint' ? undefined : { display: 'none' }}>
        <label className="step-inspector">Inspect process step
          <select value={selectedNodeId ?? ''} onChange={event => setSelectedNodeId(event.target.value || null)}>
            <option value="">Select a step to review</option>
            {overlay.nodes.map(node => <option key={node.node_id} value={node.node_id}>{labelsById[node.node_id] ?? node.node_id}</option>)}
          </select>
        </label>
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
          artifact={selectedArtifact}
          processId={processId}
          onGenerateAgent={() => handleGenerateAgent(selectedNodeId ?? '')}
          generatingAgent={generatingAgentNodeId !== null && generatingAgentNodeId === selectedNodeId}
          canGenerateAgent={isEditor}
        />
      </div>
      </div>

      <div id="panel-agents" role="tabpanel" aria-labelledby="tab-agents" tabIndex={0} hidden={activeTab !== 'agents'}>
      {activeTab === 'agents' && (
        <AgentCardsPanel
          groups={agentGroups}
          labelsById={labelsById}
          selectedNodeId={selectedNodeId}
          onSelect={setSelectedNodeId}
          onGenerateAgent={handleGenerateAgent}
          generatingNodeId={generatingAgentNodeId}
          canGenerateAgent={isEditor}
          processId={processId}
        />
      )}
      </div>

      <div id="panel-twin" role="tabpanel" aria-labelledby="tab-twin" tabIndex={0} hidden={activeTab !== 'twin'}>
      {activeTab === 'twin' && overlay && (
        <DigitalTwinPreview overlay={overlay} groups={agentGroups} labelsById={labelsById} />
      )}
      </div>

      <div id="panel-twin-runs" role="tabpanel" aria-labelledby="tab-twin-runs" tabIndex={0} hidden={activeTab !== 'twin-runs'}>
      {activeTab === 'twin-runs' && processId && <DigitalTwinPanel processId={processId} groups={agentGroups} />}
      </div>
    </div>
  )
}
