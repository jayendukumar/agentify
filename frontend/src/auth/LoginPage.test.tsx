import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import LoginPage from './LoginPage'

afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals() })

describe('Login introduction', () => {
  it('keeps the form hidden for ten seconds, then focuses the name field', () => {
    vi.useFakeTimers()
    render(<LoginPage />)
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(9999))
    expect(screen.queryByRole('textbox', { name: 'Name' })).not.toBeInTheDocument()
    act(() => vi.advanceTimersByTime(1))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
  })

  it('uses a static approved logo for reduced motion and allows skipping', () => {
    vi.stubGlobal('matchMedia', () => ({ matches: true, addEventListener: vi.fn(), removeEventListener: vi.fn() }))
    render(<LoginPage />)
    expect(screen.getByRole('img', { name: 'Axyntro' })).toHaveAttribute('src', '/brand/axyntro-horizontal-violet-spark.png')
    fireEvent.click(screen.getByRole('button', { name: 'Skip introduction' }))
    expect(screen.getByRole('textbox', { name: 'Name' })).toHaveFocus()
    fireEvent.change(screen.getByRole('combobox', { name: 'Role' }), { target: { value: 'editor' } })
    expect(screen.getByText(/upload documents, edit diagrams/i)).toBeInTheDocument()
  })
})
