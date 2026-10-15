import { useEffect, useState } from 'react'
import logoAnimation from '../../brandassets/axyntro-logo-animation-white (1).gif'
import { ApiError, getAuthConfig, ssoLoginUrl } from '../api/client'
import type { AuthConfig, Role } from '../api/types'
import { ROLE_INFO } from './roles'
import BrandLogo from '../components/BrandLogo'
import { useAuth } from './AuthContext'
import './LoginPage.css'

// Epic 37, US37.7-37.8: why the API sent the browser back here instead of
// signing it in (app/api/oidc.py's `auth_error` codes).
const SSO_ERRORS: Record<string, string> = {
  mfa_required:
    "Your sign-in didn't use multi-factor authentication, which this workspace requires. Sign in again and complete the second step. If you weren't asked for one, ask your IT team to require MFA for this app.",
  not_provisioned: "You don't have an account here yet. Ask an administrator for access.",
  deactivated: 'Your account here is deactivated. Ask an administrator if you need access again.',
  state_mismatch: 'That sign-in was started in another browser or tab. Please sign in again.',
  expired: 'That sign-in expired. Please sign in again.',
  idp_error: 'Your identity provider cancelled or refused the sign-in.',
  idp_unavailable: "The sign-in service can't be reached right now. Try again in a few minutes.",
}
const SSO_ERROR_FALLBACK = "Your sign-in couldn't be verified. Try again, and contact your administrator if it keeps happening."

// Read once and removed from the address bar, so a reload doesn't repeat it.
function takeSsoError(): string | null {
  const params = new URLSearchParams(window.location.search)
  const code = params.get('auth_error')
  if (!code) return null
  params.delete('auth_error')
  const rest = params.toString()
  window.history.replaceState(null, '', `${window.location.pathname}${rest ? `?${rest}` : ''}${window.location.hash}`)
  return SSO_ERRORS[code] ?? SSO_ERROR_FALLBACK
}

export default function LoginPage() {
  const { login } = useAuth()
  const [ssoError] = useState(takeSsoError)
  const [config, setConfig] = useState<AuthConfig | null>(null)
  const [showIntro, setShowIntro] = useState(() => ssoError === null)
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
    // Until this answers (or if it fails) the page offers local sign-in;
    // the server refuses it in any mode but local.
    getAuthConfig().then(setConfig).catch(() => setConfig(null))
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
        {config?.auth_mode === 'none' ? (
          // Epic 38, US38.6: a new organization whose admins haven't
          // connected their identity provider yet.
          <p className="meta" role="status">
            Sign-in isn't set up for this organization yet. Your administrator needs to connect your organization's
            identity provider first.
          </p>
        ) : config?.sso_login_url ? (
          <>
            <p className="meta">
              Sign in with your organization account. What you can do here comes from your groups in your organization's
              directory.
            </p>
            {ssoError && <p className="error" role="alert">{ssoError}</p>}
            <a
              className="button-link-primary login-sso-button"
              href={ssoLoginUrl(config.sso_login_url, `${window.location.pathname}${window.location.search}` || '/')}
            >
              Sign in with {config.sso_name ?? 'single sign-on'}
            </a>
          </>
        ) : (
          <>
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
          </>
        )}
      </div>
    </div>
  )
}
