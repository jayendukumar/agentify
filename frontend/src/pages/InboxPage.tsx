import { useCallback, useEffect, useState } from 'react'
import { ApiError, decideReviewTask, listInbox, listRuntimeNotifications, resolveReviewTask } from '../api/client'
import type { ReviewTask, RuntimeNotification } from '../api/types'

// Epic 39, US39.10: live cases waiting for a person. A checkpoint is the
// agent asking before it acts: approving or rejecting it lets the case go
// on. An escalation is a case the agent passed on: you finish it by hand
// and record the outcome.

function when(value: string): string {
  return new Date(value.endsWith('Z') ? value : `${value}Z`).toLocaleString()
}

function Fields({ title, values }: { title: string; values: Record<string, unknown> }) {
  const entries = Object.entries(values)
  if (entries.length === 0) return null
  return (
    <div>
      <h3 className="meta">{title}</h3>
      <dl className="inbox-fields">
        {entries.map(([key, value]) => (
          <div key={key}>
            <dt>{key}</dt>
            <dd>{typeof value === 'object' ? JSON.stringify(value) : String(value)}</dd>
          </div>
        ))}
      </dl>
    </div>
  )
}

function CheckpointActions({ task, onDone }: { task: ReviewTask; onDone: (task: ReviewTask) => void }) {
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function decide(decision: 'approve' | 'reject') {
    if (decision === 'reject' && !comment.trim()) {
      setError('Say why you are rejecting it.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      onDone(await decideReviewTask(task.process_id, task.id, decision, comment))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the decision')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="inbox-actions">
      <label>
        Comment
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
      </label>
      <button type="button" disabled={busy} onClick={() => decide('approve')}>Approve</button>
      <button type="button" className="button-secondary" disabled={busy} onClick={() => decide('reject')}>Reject</button>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}

function ResolveActions({ task, onDone }: { task: ReviewTask; onDone: (task: ReviewTask) => void }) {
  const [decision, setDecision] = useState<'approve' | 'reject' | ''>('')
  const [details, setDetails] = useState('')
  const [comment, setComment] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function resolve() {
    let outcome: Record<string, unknown> = {}
    if (details.trim()) {
      try {
        const parsed: unknown = JSON.parse(details)
        if (typeof parsed !== 'object' || parsed === null || Array.isArray(parsed)) throw new Error('not an object')
        outcome = parsed as Record<string, unknown>
      } catch {
        setError('Outcome details must be a JSON object, like {"accountId": "ACC-1"}.')
        return
      }
    }
    if (decision) outcome = { ...outcome, decision }
    if (Object.keys(outcome).length === 0) {
      setError('Record the outcome you reached.')
      return
    }
    setBusy(true)
    setError(null)
    try {
      onDone(await resolveReviewTask(task.process_id, task.id, outcome, comment))
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Could not record the outcome')
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="inbox-actions">
      <label>
        Outcome
        <select value={decision} onChange={(e) => setDecision(e.target.value as 'approve' | 'reject' | '')}>
          <option value="">(no decision)</option>
          <option value="approve">Approved</option>
          <option value="reject">Rejected</option>
        </select>
      </label>
      <label>
        Outcome details (JSON)
        <textarea value={details} onChange={(e) => setDetails(e.target.value)} rows={2} />
      </label>
      <label>
        Comment
        <textarea value={comment} onChange={(e) => setComment(e.target.value)} rows={2} />
      </label>
      <button type="button" disabled={busy} onClick={resolve}>Resolve case</button>
      {error && <p className="error" role="alert">{error}</p>}
    </div>
  )
}

function TaskCard({ task, onDone }: { task: ReviewTask; onDone: (task: ReviewTask) => void }) {
  const open = task.status === 'open'
  return (
    <li className="inbox-task">
      <div className="inbox-task-head">
        <strong>{task.agent_name}</strong>
        <span className="badge">{task.kind === 'checkpoint' ? 'Asks before acting' : 'Passed on to a person'}</span>
        {task.overdue && <span className="badge status-failed">Overdue</span>}
        {task.level > 0 && <span className="badge">Escalated {task.level}x</span>}
        {!open && <span className="badge status-done">{task.decision ?? task.status}</span>}
      </div>
      <p>{task.summary}</p>
      <p className="meta">
        {task.assignee_role ? `For ${task.assignee_role}` : 'Any reviewer'} · due {when(task.due_at)}
      </p>
      <Fields title="Proposed action" values={task.proposed_action} />
      <Fields title="Case" values={task.case_input} />
      {!open && task.comment && <p className="meta">Comment: {task.comment}</p>}
      {open && task.kind === 'checkpoint' && <CheckpointActions task={task} onDone={onDone} />}
      {open && task.kind === 'escalation' && <ResolveActions task={task} onDone={onDone} />}
    </li>
  )
}

export default function InboxPage() {
  const [tasks, setTasks] = useState<ReviewTask[] | null>(null)
  const [notes, setNotes] = useState<RuntimeNotification[]>([])
  const [showClosed, setShowClosed] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(() => {
    listInbox(showClosed)
      .then(setTasks)
      .catch((err) => setError(err instanceof ApiError ? err.message : 'Failed to load the inbox'))
    listRuntimeNotifications().then(setNotes).catch(() => setNotes([]))
  }, [showClosed])

  useEffect(load, [load])

  function done(updated: ReviewTask) {
    setTasks((current) =>
      (current ?? [])
        .map((t) => (t.id === updated.id ? updated : t))
        .filter((t) => showClosed || t.status === 'open'),
    )
  }

  return (
    <div className="page">
      <h1>Inbox</h1>
      <p className="meta">
        Live cases waiting for a person. Approving or rejecting a checkpoint lets the agent carry on; resolving an
        escalated case closes it with the outcome you record. You can't review a case you asked for.
      </p>
      <label className="meta">
        <input type="checkbox" checked={showClosed} onChange={(e) => setShowClosed(e.target.checked)} /> Show decided
        and resolved tasks
      </label>
      {notes.length > 0 && (
        <section aria-label="Notifications">
          <h2>Notifications</h2>
          <ul className="inbox-notes">
            {notes.slice(0, 5).map((note) => (
              <li key={note.id}>
                <span className="meta">{when(note.created_at)}</span> {note.message}
              </li>
            ))}
          </ul>
        </section>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      {tasks === null && !error && <p className="loading-state" role="status">Loading...</p>}
      {tasks !== null && tasks.length === 0 && <p className="empty-state">Nothing is waiting for you.</p>}
      {tasks !== null && tasks.length > 0 && (
        <ul className="inbox-list">
          {tasks.map((task) => (
            <TaskCard key={task.id} task={task} onDone={done} />
          ))}
        </ul>
      )}
    </div>
  )
}
