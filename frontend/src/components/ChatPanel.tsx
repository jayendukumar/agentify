import { useEffect, useState } from 'react'
import { ApiError, applyChatMessage, listChatMessages, sendChatMessage } from '../api/client'
import type { ChatMessageResult, DiagramDiffOperation } from '../api/types'

function describeOperation(op: DiagramDiffOperation): string {
  switch (op.op) {
    case 'add_element':
      return `Add "${op.element?.label ?? '?'}" (${op.element?.type ?? 'task'})`
    case 'remove_element':
      return `Remove ${op.element_id}`
    case 'update_element':
      return `Update ${op.element_id}: ${JSON.stringify(op.fields)}`
    case 'add_flow':
      return `Connect ${op.flow?.from ?? '?'} → ${op.flow?.to ?? '?'}`
    case 'remove_flow':
      return `Remove connection ${op.flow_id}`
    case 'update_flow':
      return `Reroute ${op.flow_id}: ${JSON.stringify(op.fields)}`
    default:
      return op.op
  }
}

export default function ChatPanel({
  processId,
  selectedElementId,
  dirty,
  onApplied,
}: {
  processId: string
  selectedElementId: string | null
  dirty: boolean
  onApplied: () => void
}) {
  const [messages, setMessages] = useState<ChatMessageResult[]>([])
  const [loadError, setLoadError] = useState<string | null>(null)
  const [text, setText] = useState('')
  const [sending, setSending] = useState(false)
  const [decidingId, setDecidingId] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    listChatMessages(processId)
      .then((loaded) => {
        if (!cancelled) setMessages(loaded)
      })
      .catch((err) => {
        if (!cancelled) setLoadError(err instanceof ApiError ? err.message : 'Failed to load chat history')
      })
    return () => {
      cancelled = true
    }
  }, [processId])

  async function handleSend(event: React.FormEvent) {
    event.preventDefault()
    const trimmed = text.trim()
    if (!trimmed || sending) return

    setSending(true)
    setActionError(null)
    try {
      const reply = await sendChatMessage(processId, trimmed, selectedElementId)
      setMessages((prev) => [...prev, reply])
      setText('')
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to send message')
    } finally {
      setSending(false)
    }
  }

  async function handleDecide(messageId: string, confirm: boolean) {
    setDecidingId(messageId)
    setActionError(null)
    try {
      const updated = await applyChatMessage(processId, messageId, confirm)
      setMessages((prev) => prev.map((m) => (m.id === messageId ? updated : m)))
      if (confirm) onApplied()
    } catch (err) {
      setActionError(err instanceof ApiError ? err.message : 'Failed to apply change')
    } finally {
      setDecidingId(null)
    }
  }

  return (
    <aside className="chat-panel" aria-label="Diagram assistant">
      <h2>Diagram assistant</h2>
      <div className="chat-messages" role="log" aria-label="Conversation" aria-live="polite">
        {messages.length === 0 && !loadError && (
          <p className="meta">Ask about the diagram, or describe a change (e.g. &quot;add a review step after approval&quot;).</p>
        )}
        {loadError && <p className="error" role="alert">{loadError}</p>}
        {messages.map((message) => (
          <div key={message.id} className="chat-turn">
            <div className="chat-message chat-message-user">{message.request_text}</div>
            <div className="chat-message chat-message-assistant">
              {message.reply_text}
              {message.kind === 'edit' && message.proposed_diff && (
                <div className="chat-diff-preview">
                  <div className="element-header">
                    <span className="badge">{message.proposed_diff.intent.replace('_', ' ')}</span>
                    {message.applied && <span className="badge confidence-high">applied</span>}
                    {message.declined && <span className="badge">declined</span>}
                  </div>
                  <ul className="flow-list">
                    {message.proposed_diff.operations.map((op, index) => (
                      <li key={index}>{describeOperation(op)}</li>
                    ))}
                  </ul>
                  {message.needs_confirmation && !message.applied && !message.declined && (
                    <div className="chat-diff-actions">
                      <button
                        type="button"
                        onClick={() => handleDecide(message.id, true)}
                        disabled={decidingId === message.id}
                      >
                        {decidingId === message.id ? 'Applying...' : 'Confirm'}
                      </button>
                      <button
                        type="button"
                        onClick={() => handleDecide(message.id, false)}
                        disabled={decidingId === message.id}
                      >
                        Reject
                      </button>
                    </div>
                  )}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>

      {actionError && <p className="error" role="alert">{actionError}</p>}

      <form className="chat-input-row" onSubmit={handleSend}>
        <input
          type="text"
          aria-label="Message to diagram assistant"
          value={text}
          onChange={(event) => setText(event.target.value)}
          placeholder={dirty ? 'Save or discard your manual edits first' : 'Ask or describe a change...'}
          disabled={dirty || sending}
        />
        <button type="submit" disabled={dirty || sending || !text.trim()}>
          {sending ? 'Sending...' : 'Send'}
        </button>
      </form>
    </aside>
  )
}
