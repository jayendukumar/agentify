import { useEffect, useState } from 'react'
import logoAnimation from '../../brandassets/axyntro-logo-animation-white (1).gif'
import { ApiError } from '../api/client'
import type { Role } from '../api/types'
import BrandLogo from '../components/BrandLogo'
import { useAuth } from './AuthContext'
import './LoginPage.css'

export default function LoginPage() {
  const { login } = useAuth()
  const [showIntro, setShowIntro] = useState(true)
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  const [name, setName] = useState('')
  const [role, setRole] = useState<Role>('viewer')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const timer = window.setTimeout(() => setShowIntro(false), 10000)
    return () => window.clearTimeout(timer)
  }, [])

  useEffect(() => {
    const preference = window.matchMedia?.('(prefers-reduced-motion: reduce)')
    if (!preference) return
    const update = () => setReducedMotion(preference.matches)
    preference.addEventListener('change', update)
    return () => preference.removeEventListener('change', update)
  }, [])

  async function handleSubmit(event: React.FormEvent) {
    event.preventDefault()
    if (!name.trim()) return
    setSubmitting(true)
    setError(null)
    try {
      await login(name.trim(), role)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'Failed to log in')
    } finally {
      setSubmitting(false)
    }
  }

  if (showIntro) {
    return (
      <div className="login-intro" aria-label="Welcome to Axyntro">
        {reducedMotion
          ? <BrandLogo variant="full" className="login-intro-animation" />
          : <img src={logoAnimation} alt="Axyntro" className="login-intro-animation" />}
        <button type="button" className="login-intro-skip" onClick={() => setShowIntro(false)}>Skip introduction</button>
      </div>
    )
  }

  return (
    <div className="login-page">
      <div className="login-card">
        <BrandLogo variant="full" className="login-logo" />
        <h1>Welcome to Axyntro</h1>
        {/* Brand standards s.10 working tagline, plus the functional product
            descriptor that appears in the header nav. */}
        <p className="login-tagline">From process to intelligent action.</p>
        <p className="login-subtagline meta">Agentic Solution Generator</p>
        <p className="meta">
          Enter your name and choose your role to continue. Your selection applies each time you log in.
        </p>
        <form onSubmit={handleSubmit} className="login-form">
          <label>
            Name
            <input
              type="text"
              name="name"
              autoComplete="name"
              required
              disabled={submitting}
              value={name}
              onChange={(event) => setName(event.target.value)}
              placeholder="e.g. Alex"
              autoFocus
            />
          </label>
          <label>
            Role
            <select value={role} disabled={submitting} aria-describedby="role-help" onChange={(event) => setRole(event.target.value as Role)}>
              <option value="viewer">Viewer</option>
              <option value="editor">Editor</option>
            </select>
          </label>
          <p id="role-help" className="meta">{role === 'editor' ? 'Upload documents, edit diagrams, finalize processes and override recommendations.' : 'View processes, diagrams and results. Editing and finalizing require Editor access.'}</p>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" disabled={submitting || !name.trim()}>
            {submitting ? 'Logging in...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
