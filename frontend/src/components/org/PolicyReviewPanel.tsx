import { useEffect, useRef, useState } from 'react'
import {
  ApiError,
  bulkConfirmContextEntities,
  confirmContextEntity,
  dismissContextEntity,
  listContextEntities,
  listPolicyDocuments,
  uploadPolicyDocument,
} from '../../api/client'
import type { ContextDocument, ContextEntity } from '../../api/types'
import { describeEntity, KIND_LABELS } from '../../lib/contextLabels'

interface Props {
  businessUnits: ContextEntity[]
  canEdit: boolean
  onChanged: () => void
}

function confidenceClass(confidence: number | null): string {
  if (confidence === null) return 'confidence-medium'
  if (confidence >= 0.8) return 'confidence-high'
  if (confidence >= 0.6) return 'confidence-medium'
  return 'confidence-low'
}

/** US27.6: upload policy documents, then confirm or dismiss what was
 * extracted. Nothing extracted takes effect until confirmed. */
export default function PolicyReviewPanel({ businessUnits, canEdit, onChanged }: Props) {
  const [documents, setDocuments] = useState<ContextDocument[]>([])
  const [proposals, setProposals] = useState<ContextEntity[]>([])
  const [title, setTitle] = useState('')
  const [code, setCode] = useState('')
  const [unit, setUnit] = useState('')
  const [jurisdictions, setJurisdictions] = useState('')
  const [effectiveDate, setEffectiveDate] = useState('')
  const [uploading, setUploading] = useState(false)
  const [busyId, setBusyId] = useState<string | null>(null)
  const [message, setMessage] = useState<{ kind: 'info' | 'error'; text: string } | null>(null)
  const fileRef = useRef<HTMLInputElement>(null)

  async function refresh() {
    try {
      const [docs, pending] = await Promise.all([listPolicyDocuments(), listContextEntities(null, 'proposed')])
      setDocuments(docs)
      setProposals(pending)
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Could not load policy documents' })
    }
  }

  useEffect(() => {
    void refresh()
  }, [])

  useEffect(() => {
    if (!documents.some((d) => d.status === 'pending' || d.status === 'processing')) return
    const interval = setInterval(refresh, 4000)
    return () => clearInterval(interval)
  }, [documents])

  async function handleUpload(event: React.FormEvent) {
    event.preventDefault()
    const file = fileRef.current?.files?.[0]
    if (!file) {
      setMessage({ kind: 'error', text: 'Choose a policy document to upload.' })
      return
    }
    setUploading(true)
    setMessage(null)
    try {
      await uploadPolicyDocument(file, { title, code, business_unit_id: unit, jurisdictions, effective_date: effectiveDate })
      setTitle('')
      setCode('')
      if (fileRef.current) fileRef.current.value = ''
      setMessage({ kind: 'info', text: 'Uploaded. Rules will appear below for review once extraction finishes.' })
      await refresh()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Upload failed' })
    } finally {
      setUploading(false)
    }
  }

  async function act(id: string, action: () => Promise<unknown>) {
    setBusyId(id)
    setMessage(null)
    try {
      await action()
      await refresh()
      onChanged()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'That action failed' })
    } finally {
      setBusyId(null)
    }
  }

  function handleConfirm(proposal: ContextEntity) {
    if (proposal.relaxes) {
      const ok = window.confirm(
        `"${proposal.name}" would loosen an existing rule (for example removing a required human review or raising an approval limit). Confirm this change?`,
      )
      if (!ok) return
    }
    void act(proposal.id, () => confirmContextEntity(proposal.id, proposal.relaxes))
  }

  async function handleBulkConfirm() {
    setBusyId('bulk')
    setMessage(null)
    try {
      const result = await bulkConfirmContextEntities()
      const skipped = Object.keys(result.skipped).length
      setMessage({
        kind: 'info',
        text: `Confirmed ${result.confirmed.length}.${skipped ? ` ${skipped} left for individual review (low confidence or would loosen a rule).` : ''}`,
      })
      await refresh()
      onChanged()
    } catch (err) {
      setMessage({ kind: 'error', text: err instanceof ApiError ? err.message : 'Bulk confirm failed' })
    } finally {
      setBusyId(null)
    }
  }

  return (
    <section aria-labelledby="policies-heading">
      <h2 id="policies-heading">Policy documents</h2>
      <p className="meta privacy-notice">
        Policy text is sent to the configured LLM provider to extract rules. Check your provider choice before uploading
        confidential policies.
      </p>
      <form className="org-upload-form" onSubmit={handleUpload}>
        <fieldset disabled={!canEdit || uploading}>
          <legend>Upload a policy</legend>
          <label>File<input ref={fileRef} type="file" accept=".pdf,.docx,.txt,.md" /></label>
          <label>Title<input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Expense policy" /></label>
          <label>
            Reference code
            <input value={code} onChange={(e) => setCode(e.target.value)} placeholder="e.g. FIN-004 (re-uploading the same code creates a new version)" />
          </label>
          <label>
            Applies to business unit
            <select value={unit} onChange={(e) => setUnit(e.target.value)}>
              <option value="">Whole organization</option>
              {businessUnits.map((u) => <option key={u.id} value={u.id}>{u.name}</option>)}
            </select>
          </label>
          <label>Applies in<input value={jurisdictions} onChange={(e) => setJurisdictions(e.target.value)} placeholder="e.g. EU, UK (empty = everywhere)" /></label>
          <label>Effective from<input type="date" value={effectiveDate} onChange={(e) => setEffectiveDate(e.target.value)} /></label>
          <button type="submit" className="button-primary">{uploading ? 'Uploading...' : 'Upload and extract rules'}</button>
        </fieldset>
      </form>
      {message && <p className={message.kind} role={message.kind === 'error' ? 'alert' : 'status'}>{message.text}</p>}

      {documents.length > 0 && (
        <ul className="document-list">
          {documents.map((doc) => (
            <li key={doc.id}>
              <span className="document-row">
                <span>{doc.filename}</span>
                <span className={`status status-${doc.status === 'pending' ? 'queued' : doc.status}`}>{doc.status}</span>
                {doc.status === 'done' && <span className="meta">{doc.proposal_count} rules proposed</span>}
              </span>
              {doc.status === 'failed' && <span className="status-note error">{doc.error_message}</span>}
            </li>
          ))}
        </ul>
      )}

      <h3>Waiting for review ({proposals.length})</h3>
      {proposals.length === 0 ? (
        <p className="empty-state">Nothing to review. Extracted rules appear here until someone confirms or dismisses them.</p>
      ) : (
        <>
          <button type="button" className="button-secondary" disabled={!canEdit || busyId !== null} onClick={handleBulkConfirm}>
            {busyId === 'bulk' ? 'Confirming...' : 'Confirm all high-confidence'}
          </button>
          <ul className="registry-entry-list">
            {proposals.map((proposal) => (
              <li key={proposal.id} className="registry-entry-card">
                <div className="registry-entry-header">
                  <span className="badge">{KIND_LABELS[proposal.kind].singular}</span>
                  <strong>{proposal.name}</strong>
                  {proposal.code && <span className="meta">{proposal.code}</span>}
                  <span className={`badge ${confidenceClass(proposal.confidence)}`}>
                    {proposal.confidence === null ? 'confidence unknown' : `${Math.round(proposal.confidence * 100)}% confident`}
                  </span>
                  {proposal.supersedes_id && <span className="badge confidence-medium">Updates an existing rule</span>}
                  {proposal.relaxes && <span className="badge confidence-low">Loosens an existing rule</span>}
                </div>
                <p>{describeEntity(proposal)}</p>
                {proposal.citations.map((citation, index) => (
                  <blockquote key={index} className="org-citation">
                    <span className="meta">{citation.clause_ref}</span> {citation.quote}
                  </blockquote>
                ))}
                <div className="registry-entry-actions">
                  <button type="button" className="button-primary" disabled={!canEdit || busyId !== null} onClick={() => handleConfirm(proposal)}>
                    {busyId === proposal.id ? 'Saving...' : 'Confirm'}
                  </button>
                  <button
                    type="button"
                    className="button-secondary"
                    disabled={!canEdit || busyId !== null}
                    onClick={() => act(proposal.id, () => dismissContextEntity(proposal.id))}
                  >
                    Dismiss
                  </button>
                </div>
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  )
}
