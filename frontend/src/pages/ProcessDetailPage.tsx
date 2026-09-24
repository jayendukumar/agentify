import { useEffect, useRef, useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { ApiError, deleteDocument, generateBpmn, getBlueprint, getProcess, listDocuments, listGapFindings, uploadDocuments } from '../api/client'
import type { DocumentSummary, ProcessDetail } from '../api/types'
import { useAuth } from '../auth/AuthContext'
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
  const { user } = useAuth()
  const { processId } = useParams<{ processId: string }>()
  const [process, setProcess] = useState<ProcessDetail | null>(null)
  const [documents, setDocuments] = useState<DocumentSummary[]>([])
  const [hasBlueprint, setHasBlueprint] = useState(false)
  const [openGapCount, setOpenGapCount] = useState(0)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [uploading, setUploading] = useState(false)
  const [bpmnAction, setBpmnAction] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const [generatingBpmn, setGeneratingBpmn] = useState(false)
  const [deletingDocId, setDeletingDocId] = useState<string | null>(null)
  const [deleteError, setDeleteError] = useState<string | null>(null)
  const fileInputRef = useRef<HTMLInputElement>(null)

  async function refresh() {
    if (!processId) return
    setLoadError(null)
    try {
      const [processDetail, documentList, blueprint, openGaps] = await Promise.all([
        getProcess(processId),
        listDocuments(processId),
        getBlueprint(processId),
        listGapFindings(processId, 'open'),
      ])
      setProcess(processDetail)
      setDocuments(documentList)
      setHasBlueprint(blueprint !== null)
      setOpenGapCount(openGaps.length)
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

  const hasPendingDocuments = documents.some((doc) => doc.status === 'queued' || doc.status === 'processing')
  const allDocumentsProcessed = documents.length > 0 && documents.every((doc) => doc.status === 'done')

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

  async function handleDeleteDocument(documentId: string, filename: string) {
    if (!processId) return
    if (!window.confirm(`Delete "${filename}"? This cannot be undone.`)) return

    setDeletingDocId(documentId)
    setDeleteError(null)
    try {
      await deleteDocument(processId, documentId)
      await refresh()
    } catch (err) {
      setDeleteError(err instanceof ApiError ? err.message : 'Could not delete the document. Please try again.')
    } finally {
      setDeletingDocId(null)
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
  if (loadError && !process) {
    return (
      <div className="page">
        <p>
          <Link to="/">&larr; All processes</Link>
        </p>
        <p className="error" role="alert">{loadError}</p>
        <button type="button" onClick={refresh}>Try again</button>
      </div>
    )
  }
  if (!process) return <p className="loading-state" role="status">Loading process...</p>

  return (
    <div className="page">
      <p>
        <Link to="/">&larr; All processes</Link>
      </p>
      <h1>{process.name}</h1>
      <p className="meta">Build a reviewed process baseline, then explore its automation opportunities.</p>
      {loadError && <div><p className="error" role="alert">{loadError}</p><button type="button" onClick={refresh}>Try again</button></div>}

      <nav className="page-actions" aria-label="Process navigation">
      {process.has_draft_bpmn && (
        <p>
          <Link className="button-link button-link-primary" to={`/processes/${processId}/diagram`}>Open diagram &rarr;</Link>
        </p>
      )}

      {(process.document_count > 0 || openGapCount > 0) && (
        <p>
          <Link to={`/processes/${processId}/gaps`}>
            Review gaps{openGapCount > 0 ? ` (${openGapCount})` : ''} &rarr;
          </Link>
        </p>
      )}
      </nav>

      <ProcessStepper steps={buildSteps(process, hasBlueprint)} />

      {!process.has_draft_bpmn && process.document_count > 0 && (
        <div className="next-step-action">
          {hasPendingDocuments && (
            <div className="processing-progress" role="status" aria-live="polite">
              <div className="processing-progress-header">
                <span>Processing uploaded documents</span>
                <span className="meta">Please wait...</span>
              </div>
              <div
                className="processing-progress-bar"
                role="progressbar"
                aria-label="Document processing progress"
                aria-valuetext="Processing uploaded documents"
              />
            </div>
          )}
          <button
            type="button"
            className="button-primary"
            onClick={handleGenerateBpmn}
            disabled={generatingBpmn || !allDocumentsProcessed || user?.role !== 'editor'}
            title={user?.role !== 'editor' ? 'Editor access required' : !allDocumentsProcessed ? 'Document processing must complete first' : undefined}
          >
            {generatingBpmn ? 'Generating...' : 'Generate draft BPMN'}
          </button>
          {!hasPendingDocuments && !allDocumentsProcessed && documents.length > 0 && (
            <p className="error" role="alert">BPMN generation is unavailable until all documents finish processing.</p>
          )}
          {bpmnAction && <p role={bpmnAction.kind === 'error' ? 'alert' : 'status'} className={bpmnAction.kind === 'error' ? 'error' : 'info'}>{bpmnAction.text}</p>}
        </div>
      )}

      <h2>Documents</h2>
      {/* US10.5: documents are sent to the configured LLM provider for
          extraction -- see planning/claude-api-access-notes.md's "Data
          privacy decision" section before uploading real sensitive
          business documents against the current default provider. */}
      <p className="meta privacy-notice">
        Uploaded documents are sent to the configured LLM provider (currently OpenRouter -&gt; Qwen3.7 Flash) for
        extraction. Avoid uploading highly sensitive documents with the current default provider.
      </p>
      <button type="button" className={process.document_count === 0 ? 'button-primary' : 'button-secondary'} onClick={() => fileInputRef.current?.click()} disabled={uploading || user?.role !== 'editor'} aria-describedby="upload-help">
        {uploading ? 'Uploading...' : 'Upload documents'}
      </button>
        <input
          ref={fileInputRef}
          type="file"
          multiple
          accept=".pdf,.docx,.vsdx,.png,.jpg,.jpeg"
          onChange={handleUpload}
          disabled={uploading || user?.role !== 'editor'}
          hidden
          aria-label="Choose documents"
        />
      <p id="upload-help" className="meta">PDF, Word, Visio, PNG or JPEG. You can select multiple files.{user?.role !== 'editor' ? ' Editor access is required to upload.' : ''}</p>

      {documents.length === 0 && <p className="empty-state">No documents uploaded yet. Add source documentation to begin extracting this process.</p>}
      {deleteError && <p className="error" role="alert">{deleteError}</p>}

      <ul className="document-list">
        {documents.map((doc) => (
          <li key={doc.id}>
            <span className="document-row">
              <span>{doc.filename}</span>
              <span className={`status status-${doc.status}`}>{doc.status}</span>
              <span className="meta">{(doc.size_bytes / 1024).toFixed(1)} KB</span>
              {doc.status === 'failed' && user?.role === 'editor' && (
                <button
                  type="button"
                  className="button-secondary document-delete-button"
                  onClick={() => handleDeleteDocument(doc.id, doc.filename)}
                  disabled={deletingDocId === doc.id}
                >
                  {deletingDocId === doc.id ? 'Deleting...' : 'Delete'}
                </button>
              )}
            </span>
            {doc.status === 'queued' && <span className="status-note">Waiting to start processing...</span>}
            {doc.status === 'processing' && (
              <span className="status-note">Extracting the process -- this can take a minute or two for larger documents.</span>
            )}
            {doc.status === 'failed' && (
              <span className="status-note error">
                {doc.validation_message || 'Processing failed for this document.'}
              </span>
            )}
            {doc.status === 'done' && doc.process_definition_confidence !== null && (
              <span className="status-note">
                Process definition confidence: {doc.process_definition_confidence}%
                {doc.validation_message ? ` — ${doc.validation_message}` : ''}
              </span>
            )}
          </li>
        ))}
      </ul>

      {process.process_schema && <ProcessSchemaView schema={process.process_schema} />}
    </div>
  )
}
