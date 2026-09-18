import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, generateBpmn, getBlueprint, getProcess, listDocuments, uploadDocuments } from '../api/client'
import type { DocumentSummary, ProcessDetail } from '../api/types'
import ProcessSchemaView from '../components/ProcessSchemaView'
import ProcessStepper, { type StepperStep } from '../components/ProcessStepper'

function buildSteps(process: ProcessDetail, hasBlueprint: boolean): StepperStep[] {
  const conditions = [
    process.document_count > 0,
    process.has_draft_bpmn,
    process.finalized_version_count > 0,
    hasBlueprint,
  ]
  const currentIndex = conditions.findIndex((done) => !done)

  const statusFor = (index: number): StepperStep['status'] => {
    if (conditions[index]) return 'done'
    return index === currentIndex ? 'current' : 'upcoming'
  }

  return [
    {
      label: 'Upload documents',
      description: 'Add the source documentation this process should be built from.',
      status: statusFor(0),
    },
    {
      label: 'Generate draft BPMN',
      description: 'Extract the process from the uploaded documents into an editable diagram.',
      status: statusFor(1),
    },
    {
      label: 'Review & finalize',
      description: 'Review the draft, edit it, then lock it in as the as-is baseline.',
      status: statusFor(2),
    },
    {
      label: 'Generate agentic blueprint',
      description: 'Evaluate the finalized process for AI-agent automation.',
      status: statusFor(3),
    },
  ]
}

export default function ProcessDetailPage() {
  const { processId } = useParams<{ processId: string }>()
  const [process, setProcess] = useState<ProcessDetail | null>(null)
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [hasBlueprint, setHasBlueprint] = useState(false)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [bpmnAction, setBpmnAction] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [generatingBpmn, setGeneratingBpmn] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function refresh() {
    if (!processId) return
    setLoadError(null)
    try {
      const [processDetail, documentList, blueprint] = await Promise.all([
        getProcess(processId),
        listDocuments(processId),
        getBlueprint(processId),
      ])
      setProcess(processDetail)
      setDocuments(documentList)
      setHasBlueprint(blueprint !== null)
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Failed to load process')
    }
  }

  useEffect(() => {
    refresh()
  }, [processId])

  useEffect(() => {
    const hasPendingDocument = documents.some((doc) => doc.status === 'queued' || doc.status === 'processing')
    if (!hasPendingDocument) return

    const interval = setInterval(refresh, 4000)
    return () => clearInterval(interval)
  }, [documents])

  async function handleUpload(event: React.ChangeEvent<HTMLInputElement>) {
    if (!processId || !event.target.files || event.target.files.length === 0) return

    setUploading(true)
    setLoadError(null)
    try {
      await uploadDocuments(processId, event.target.files)
      await refresh()
    } catch (err) {
      setLoadError(err instanceof ApiError ? err.message : 'Failed to upload document(s)')
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ''
    }
  }

  async function handleGenerateBpmn() {
    if (!processId) return
    setGeneratingBpmn(true)
    setBpmnAction(null)
    try {
      await generateBpmn(processId)
      setBpmnAction({ kind: 'info', text: 'Draft BPMN generated.' })
      await refresh()
    } catch (err) {
      if (err instanceof ApiError && err.status === 501) {
        setBpmnAction({
          kind: 'info',
          text: "This step isn't built yet — BPMN generation is still on the roadmap (Epic 3).",
        })
      } else {
        setBpmnAction({ kind: 'error', text: err instanceof ApiError ? err.message : 'Failed to generate BPMN' })
      }
    } finally {
      setGeneratingBpmn(false)
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
  if (!process) return <p>Loading...</p>

  return (
    <div className="page">
      <p>
        <Link to="/">&larr; All processes</Link>
      </p>
      <h2>{process.name}</h2>

      {process.has_draft_bpmn && (
        <p>
          <Link to={`/processes/${processId}/diagram`}>Open diagram &rarr;</Link>
        </p>
      )}

      <ProcessStepper steps={buildSteps(process, hasBlueprint)} />

      {!process.has_draft_bpmn && process.document_count > 0 && (
        <div className="next-step-action">
          <button type="button" onClick={handleGenerateBpmn} disabled={generatingBpmn}>
            {generatingBpmn ? 'Generating...' : 'Generate draft BPMN'}
          </button>
          {bpmnAction && <p className={bpmnAction.kind === 'error' ? 'error' : 'info'}>{bpmnAction.text}</p>}
        </div>
      )}

      <h3>Documents</h3>
      <label className="upload-button">
        {uploading ? 'Uploading...' : 'Upload documents'}
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.vsdx,.png,.jpg,.jpeg"
          onChange={handleUpload}
          disabled={uploading}
          hidden
        />
      </label>

      {documents.length === 0 && <p>No documents uploaded yet.</p>}

      <ul className="document-list">
        {documents.map((doc) => (
          <li key={doc.id}>
            <span className="document-row">
              <span>{doc.filename}</span>
              <span className={`status status-${doc.status}`}>{doc.status}</span>
              <span className="meta">{(doc.size_bytes / 1024).toFixed(1)} KB</span>
            </span>
            {doc.status === 'queued' && <span className="status-note">Waiting to start processing...</span>}
            {doc.status === 'processing' && (
              <span className="status-note">Extracting the process -- this can take a minute or two for larger documents.</span>
            )}
            {doc.status === 'failed' && <span className="status-note error">Processing failed for this document.</span>}
          </li>
        ))}
      </ul>

      {process.process_schema && <ProcessSchemaView schema={process.process_schema} />}
    </div>
  )
}
