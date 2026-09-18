import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams } from 'react-router-dom'
import { ApiError, getBpmn, getProcess, listProcesses, updateBpmn } from '../api/client'
import type { ProcessDetail, ProcessSummary } from '../api/types'
import BpmnCanvas, { type BpmnCanvasHandle } from '../components/BpmnCanvas'
import ElementDetailPanel from '../components/ElementDetailPanel'
import { downloadBlob, downloadText, svgToPngBlob } from '../lib/exportPng'

export default function DiagramPage() {
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
        <p className="error">{loadError}</p>
      </div>
    )
  }

  if (loading || !process) return <p>Loading...</p>

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
      <div className="diagram-toolbar">
        <Link to={`/processes/${processId}`}>&larr; {process.name}</Link>

        <select
          value={processId}
          onChange={(event) => navigate(`/processes/${event.target.value}/diagram`)}
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

        <span className="meta">{dirty ? 'Unsaved changes' : 'Saved'}</span>
        <button type="button" onClick={handleSave} disabled={saving || !dirty}>
          {saving ? 'Saving...' : 'Save'}
        </button>

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

      {saveMessage && <p className={saveMessage.kind === 'error' ? 'error' : 'info'}>{saveMessage.text}</p>}
      {exportError && <p className="error">{exportError}</p>}

      <div className="diagram-body">
        <BpmnCanvas
          ref={canvasRef}
          xml={xml}
          onSelectionChange={setSelectedElementId}
          onDirtyChange={handleDirtyChange}
        />
        <ElementDetailPanel elementId={selectedElementId} schema={process.process_schema} />
      </div>
    </div>
  )
}
