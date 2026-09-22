import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, finalizeProcess, generateBpmn, getBpmn, getProcess, listProcesses, updateBpmn } from '../api/client'
import type { ProcessDetail, ProcessSummary } from '../api/types'
import { useAuth } from '../auth/AuthContext'
import BpmnCanvas, { type BpmnCanvasHandle } from '../components/BpmnCanvas'
import ChatPanel from '../components/ChatPanel'
import ElementDetailPanel from '../components/ElementDetailPanel'
import { downloadBlob, downloadText, svgToPngBlob } from '../lib/exportPng'

export default function DiagramPage() {
  const { user } = useAuth()
  const isEditor = user?.role === 'editor'
  const { processId } = useParams<{ processId: string }>()
  const navigate = useNavigate()
  const canvasRef = useRef<BpmnCanvasHandle>(null)

  const [process, setProcess] = useState<ProcessDetail | null>(null)
  const [processList, setProcessList] = useState<ProcessSummary[]>([])
  const [xml, setXml] = useState<string | null>(null)
  const [hasDraft, setHasDraft] = useState(true)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)

  const [selectedElementId, setSelectedElementId] = useState<string | null>(null)
  const [dirty, setDirty] = useState(false)
  const [saving, setSaving] = useState(false)
  const [saveMessage, setSaveMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [exportError, setExportError] = useState<string | null>(null)
  const [refreshingLayout, setRefreshingLayout] = useState(false)
  const [finalizing, setFinalizing] = useState(false)

  useEffect(() => {
    if (!processId) return
    let cancelled = false

    setLoading(true)
    setLoadError(null)
    setSelectedElementId(null)
    setSaveMessage(null)

    Promise.all([getProcess(processId), getBpmn(processId), listProcesses()])
      .then(([processDetail, bpmnDoc, processes]) => {
        if (cancelled) return
        setProcess(processDetail)
        setProcessList(processes)
        setHasDraft(bpmnDoc !== null)
        setXml(bpmnDoc?.xml ?? null)
        setDirty(false)
      })
      .catch((err) => {
        if (cancelled) return
        setLoadError(err instanceof ApiError ? err.message : 'Failed to load diagram')
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [processId])

  useEffect(() => {
    if (!dirty) return
    const handler = (event: BeforeUnloadEvent) => {
      event.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [dirty])

  const handleDirtyChange = useCallback((isDirty: boolean) => {
    setDirty(isDirty)
  }, [])

  // Re-fetches the diagram + process without the full-page loading state --
  // used after a chat-applied edit (which mutates the schema + draft BPMN
  // server-side) and after Refresh Layout (POST /generate).
  const reloadDiagram = useCallback(async () => {
    if (!processId) return
    const [processDetail, bpmnDoc] = await Promise.all([getProcess(processId), getBpmn(processId)])
    setProcess(processDetail)
    setHasDraft(bpmnDoc !== null)
    setXml(bpmnDoc?.xml ?? null)
    setDirty(false)
  }, [processId])

  async function handleRefreshLayout() {
    if (!processId) return
    if (!window.confirm('Refresh the layout? This regenerates the draft from the process data and replaces manual diagram edits and positions. Export XML first if you need a copy.')) return
    setRefreshingLayout(true)
    setSaveMessage(null)
    try {
      await generateBpmn(processId)
      await reloadDiagram()
      setSaveMessage({ kind: 'info', text: 'Layout refreshed.' })
    } catch (err) {
      setSaveMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Failed to refresh layout' })
    } finally {
      setRefreshingLayout(false)
    }
  }

  async function handleFinalize() {
    if (!processId) return
    setFinalizing(true)
    setSaveMessage(null)
    try {
      const version = await finalizeProcess(processId)
      await reloadDiagram()
      setSaveMessage({ kind: 'info', text: `Finalized as version ${version.id}.` })
    } catch (err) {
      setSaveMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Failed to finalize' })
    } finally {
      setFinalizing(false)
    }
  }

  async function handleSave() {
    if (!processId || !canvasRef.current) return
    setSaving(true)
    setSaveMessage(null)
    try {
      const currentXml = await canvasRef.current.exportXml()
      const saved = await updateBpmn(processId, currentXml)
      setDirty(false)
      setSaveMessage({ kind: 'info', text: 'Diagram saved.' })
      if (saved.validation_issues.length > 0) {
        setSaveMessage({ kind: 'error', text: `Saved with warnings: ${saved.validation_issues.join('; ')}` })
      }
    } catch (err) {
      setSaveMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Failed to save diagram' })
    } finally {
      setSaving(false)
    }
  }

  async function handleExportXml() {
    if (!canvasRef.current || !process) return
    setExportError(null)
    try {
      const exported = await canvasRef.current.exportXml()
      downloadText(exported, `${process.name}.bpmn`, 'application/xml')
    } catch {
      setExportError('Failed to export XML')
    }
  }

  async function handleExportSvg() {
    if (!canvasRef.current || !process) return
    setExportError(null)
    try {
      const svg = await canvasRef.current.exportSvg()
      downloadText(svg, `${process.name}.svg`, 'image/svg+xml')
    } catch {
      setExportError('Failed to export SVG')
    }
  }

  async function handleExportPng() {
    if (!canvasRef.current || !process) return
    setExportError(null)
    try {
      const svg = await canvasRef.current.exportSvg()
      const pngBlob = await svgToPngBlob(svg)
      downloadBlob(pngBlob, `${process.name}.png`)
    } catch {
      setExportError('Failed to export PNG')
    }
  }

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

  if (loading || !process) return <p className="loading-state" role="status">Loading diagram...</p>

  if (!hasDraft || !xml) {
    return (
      <div className="page">
        <p>
          <Link to={`/processes/${processId}`}>&larr; Back to {process.name}</Link>
        </p>
        <p>No draft BPMN diagram yet for this process. Generate one first.</p>
      </div>
    )
  }

  return (
    <div className="diagram-page">
      <div className="diagram-heading">
        <h1>Process diagram</h1>
        <p className="meta">{process.name} · Review the draft, save your changes, then finalize the baseline.</p>
      </div>
      <div className="diagram-toolbar">
        <Link to={`/processes/${processId}`}>&larr; {process.name}</Link>

        <select
          value={processId}
          onChange={(event) => {
            if (!dirty || window.confirm('Switch processes and discard unsaved changes? Save first to keep your edits.')) navigate(`/processes/${event.target.value}/diagram`)
          }}
          aria-label="Switch process"
        >
          {processList.map((p) => (
            <option key={p.id} value={p.id}>
              {p.name}
            </option>
          ))}
        </select>

        <button type="button" onClick={() => canvasRef.current?.undo()} disabled={!dirty}>
          Undo
        </button>
        <button type="button" onClick={() => canvasRef.current?.redo()}>
          Redo
        </button>
        <button type="button" onClick={() => canvasRef.current?.zoomToFit()}>Fit diagram</button>

        <span className="meta">{dirty ? 'Unsaved changes' : 'Saved'}</span>
        <button type="button" className={dirty ? 'button-primary' : 'button-secondary'} onClick={handleSave} disabled={saving || !dirty || !isEditor}>
          {saving ? 'Saving...' : 'Save'}
        </button>

        <button
          type="button"
          onClick={handleRefreshLayout}
          disabled={refreshingLayout || dirty || !isEditor}
          title={!isEditor ? 'Editor access required' : 'Re-run auto-layout from the current process data -- discards manual node positions'}
        >
          {refreshingLayout ? 'Refreshing...' : 'Refresh Layout'}
        </button>

        <button
          type="button"
          onClick={handleFinalize}
          className={!dirty ? 'button-primary' : 'button-secondary'}
          disabled={finalizing || dirty || !isEditor}
          title={!isEditor ? 'Editor access required' : 'Lock the current draft in as a reviewed as-is baseline'}
        >
          {finalizing ? 'Finalizing...' : 'Finalize'}
        </button>
        <Link to={`/processes/${processId}/versions`}>Version History</Link>
        <Link to={`/processes/${processId}/blueprint`}>Agentic Blueprint</Link>
        <Link to={`/processes/${processId}/gaps`}>Gap Review</Link>

        <button type="button" onClick={handleExportXml}>
          Export XML
        </button>
        <button type="button" onClick={handleExportSvg}>
          Export SVG
        </button>
        <button type="button" onClick={handleExportPng}>
          Export PNG
        </button>
      </div>

      {saveMessage && <p role={saveMessage.kind === 'error' ? 'alert' : 'status'} className={saveMessage.kind === 'error' ? 'error' : 'info'}>{saveMessage.text}</p>}
      {exportError && <p className="error" role="alert">{exportError}</p>}

      <div className="diagram-body">
        <BpmnCanvas
          ref={canvasRef}
          xml={xml}
          onSelectionChange={setSelectedElementId}
          onDirtyChange={handleDirtyChange}
        />
        <ElementDetailPanel elementId={selectedElementId} schema={process.process_schema} />
        <ChatPanel
          processId={processId}
          selectedElementId={selectedElementId}
          dirty={dirty}
          onApplied={reloadDiagram}
        />
      </div>
    </div>
  )
}
