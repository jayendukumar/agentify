import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ChatMessageResult } from '../api/types'
import ChatPanel from './ChatPanel'

const listChatMessages = vi.fn()
const sendChatMessage = vi.fn()
const applyChatMessage = vi.fn()

vi.mock('../api/client', () => ({
  ApiError: class ApiError extends Error {
    status: number
    constructor(status: number, detail: string) {
      super(detail)
      this.status = status
    }
  },
  listChatMessages: (...args: unknown[]) => listChatMessages(...args),
  sendChatMessage: (...args: unknown[]) => sendChatMessage(...args),
  applyChatMessage: (...args: unknown[]) => applyChatMessage(...args),
}))

function explainMessage(overrides: Partial<ChatMessageResult> = {}): ChatMessageResult {
  return {
    id: 'msg-1',
    process_id: 'proc-1',
    request_text: 'who owns this step',
    selected_element_id: null,
    kind: 'explain',
    reply_text: 'The Employee role owns it.',
    proposed_diff: null,
    needs_confirmation: false,
    applied: false,
    declined: false,
    created_at: '2026-01-01T00:00:00Z',
    decided_at: null,
    ...overrides,
  }
}

function editMessage(overrides: Partial<ChatMessageResult> = {}): ChatMessageResult {
  return {
    id: 'msg-2',
    process_id: 'proc-1',
    request_text: 'rename the submit step',
    selected_element_id: null,
    kind: 'edit',
    reply_text: "Rename 'Submit request' to 'Submit approval request'.",
    proposed_diff: {
      intent: 'rename_node',
      summary: "Rename 'Submit request' to 'Submit approval request'.",
      target_element_ids: ['Task_e2'],
      operations: [{ op: 'update_element', element_id: 'Task_e2', flow_id: null, element: null, flow: null, fields: { label: 'Submit approval request' } }],
    },
    needs_confirmation: true,
    applied: false,
    declined: false,
    created_at: '2026-01-01T00:00:00Z',
    decided_at: null,
    ...overrides,
  }
}

function renderPanel(props: Partial<React.ComponentProps<typeof ChatPanel>> = {}) {
  render(
    <ChatPanel
      processId="proc-1"
      selectedElementId={null}
      dirty={false}
      onApplied={vi.fn()}
      {...props}
    />,
  )
}

describe('ChatPanel', () => {
  afterEach(() => {
    vi.clearAllMocks()
  })

  it('loads and renders chat history', async () => {
    listChatMessages.mockResolvedValue([explainMessage()])
    renderPanel()

    expect(await screen.findByText('The Employee role owns it.')).toBeInTheDocument()
    expect(screen.getByText('who owns this step')).toBeInTheDocument()
  })

  it('sends a message and appends the reply', async () => {
    listChatMessages.mockResolvedValue([])
    sendChatMessage.mockResolvedValue(explainMessage())
    const user = userEvent.setup()
    renderPanel()

    await waitFor(() => expect(listChatMessages).toHaveBeenCalled())
    await user.type(screen.getByPlaceholderText(/ask or describe/i), 'who owns this step')
    await user.click(screen.getByRole('button', { name: /^send$/i }))

    expect(await screen.findByText('The Employee role owns it.')).toBeInTheDocument()
    expect(sendChatMessage).toHaveBeenCalledWith('proc-1', 'who owns this step', null)
  })

  it('shows Confirm/Reject for an edit reply and applies on Confirm', async () => {
    const pending = editMessage()
    listChatMessages.mockResolvedValue([pending])
    applyChatMessage.mockResolvedValue(editMessage({ applied: true, needs_confirmation: true, decided_at: '2026-01-01T00:01:00Z' }))
    const onApplied = vi.fn()
    const user = userEvent.setup()
    renderPanel({ onApplied })

    await screen.findByText(pending.reply_text)
    expect(screen.getByRole('button', { name: /confirm/i })).toBeInTheDocument()

    await user.click(screen.getByRole('button', { name: /confirm/i }))

    await waitFor(() => expect(applyChatMessage).toHaveBeenCalledWith('proc-1', 'msg-2', true))
    await waitFor(() => expect(onApplied).toHaveBeenCalled())
  })

  it('rejects a proposed edit without calling onApplied', async () => {
    const pending = editMessage()
    listChatMessages.mockResolvedValue([pending])
    applyChatMessage.mockResolvedValue(editMessage({ declined: true, decided_at: '2026-01-01T00:01:00Z' }))
    const onApplied = vi.fn()
    const user = userEvent.setup()
    renderPanel({ onApplied })

    await screen.findByText(pending.reply_text)
    await user.click(screen.getByRole('button', { name: /reject/i }))

    await waitFor(() => expect(applyChatMessage).toHaveBeenCalledWith('proc-1', 'msg-2', false))
    expect(onApplied).not.toHaveBeenCalled()
  })

  it('hides Confirm/Reject once a message has already been decided', async () => {
    listChatMessages.mockResolvedValue([editMessage({ applied: true, decided_at: '2026-01-01T00:01:00Z' })])
    renderPanel()

    await screen.findByText(/rename 'submit request'/i)
    expect(screen.queryByRole('button', { name: /confirm/i })).not.toBeInTheDocument()
  })

  it('disables the input while the canvas has unsaved manual edits', async () => {
    listChatMessages.mockResolvedValue([])
    renderPanel({ dirty: true })

    await waitFor(() => expect(listChatMessages).toHaveBeenCalled())
    expect(screen.getByPlaceholderText(/save or discard/i)).toBeDisabled()
  })
})
