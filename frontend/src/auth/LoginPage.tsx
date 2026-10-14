import { useEffect, useState } from 'react'
import logoAnimation from '../../brandassets/axyntro-logo-animation-white (1).gif'
import { ApiError } from '../api/client'
import type { Role } from '../api/types'
import { ROLE_INFO } from './roles'
import BrandLogo from '../components/BrandLogo'
import { useAuth } from './AuthContext'
import './LoginPage.css'

export default function LoginPage() {
  const { login } = useAuth()
  const [showIntro, setShowIntro] = useState(true)
  const [reducedMotion, setReducedMotion] = useState(() => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches ?? false)
  const [name, setName] = useState('')
  const [roles, setRoles] = useState<Role[]>([])
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
      await login(name.trim(), roles)
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
          Enter your name and choose the roles you work in. Your selection applies each time you log in. With no
          role you can view everything but change nothing.
        </p>
        {/* Epic 37, US37.14: the server refuses this sign-in in production. */}
        <p className="local-auth-notice" role="note">Local sign-in -- not for production</p>
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
          <fieldset className="login-roles" disabled={submitting}>
            <legend>Roles</legend>
            {ROLE_INFO.map((info) => (
              <label key={info.value} className="login-role-option">
                <input
                  type="checkbox"
                  checked={roles.includes(info.value)}
                  onChange={(event) =>
                    setRoles((current) =>
                      event.target.checked ? [...current, info.value] : current.filter((r) => r !== info.value),
                    )
                  }
                />
                <span>
                  <strong>{info.label}</strong>
                  <span className="meta">{info.description}</span>
                </span>
              </label>
            ))}
          </fieldset>
          {error && <p className="error" role="alert">{error}</p>}
          <button type="submit" disabled={submitting || !name.trim()}>
            {submitting ? 'Logging in...' : 'Continue'}
          </button>
        </form>
      </div>
    </div>
  )
}
