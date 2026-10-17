import { lazy, Suspense, useEffect, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { getAuthConfig } from './api/client'
import { AuthProvider, useAuth } from './auth/AuthContext'
import LoginPage from './auth/LoginPage'
import { grantLabel } from './auth/roles'
import BrandLogo from './components/BrandLogo'
import ErrorBoundary from './components/ErrorBoundary'
import AuditLogPage from './pages/AuditLogPage'
import BatchResultsPage from './pages/BatchResultsPage'
import DatasetsPage from './pages/DatasetsPage'
import DirectoryPage from './pages/DirectoryPage'
import UsagePage from './pages/UsagePage'
import DataExportPage from './pages/DataExportPage'
import GapReviewPage from './pages/GapReviewPage'
import ProcessDetailPage from './pages/ProcessDetailPage'
import OrganizationPage from './pages/OrganizationPage'
import ProcessListPage from './pages/ProcessListPage'
import ProcessTwinPage from './pages/ProcessTwinPage'
import RegistriesPage from './pages/RegistriesPage'
import SessionsPage from './pages/SessionsPage'
import UsersPage from './pages/UsersPage'
import VersionsPage from './pages/VersionsPage'

const DiagramPage = lazy(() => import('./pages/DiagramPage'))
const BlueprintPage = lazy(() => import('./pages/BlueprintPage'))

function NotFoundPage() {
  return (
    <div className="page">
      <h1>Page not found</h1>
      <p>That page doesn't exist. Return to your processes to continue.</p>
      <p>
        <Link to="/">&larr; All processes</Link>
      </p>
    </div>
  )
}

function AppHeader() {
  const { user, logout, can } = useAuth()
  const { pathname } = useLocation()
  const [loggingOut, setLoggingOut] = useState(false)
  const [logoutError, setLogoutError] = useState(false)
  async function handleLogout() {
    setLoggingOut(true)
    setLogoutError(false)
    try { await logout() } catch { setLogoutError(true) } finally { setLoggingOut(false) }
  }
  return (
    <header className="app-header">
      <Link to="/" className="app-title-link">
        <BrandLogo variant="full" tone="white" className="app-header-logo" />
        <span className="app-header-tagline">Agentic Solution Generator</span>
      </Link>
      {user && (
        <>
          <nav className="app-header-nav" aria-label="Main navigation">
            <Link to="/" aria-current={pathname === '/' || pathname.startsWith('/processes/') ? 'page' : undefined} className="app-header-nav-link">Processes</Link>
            <NavLink to="/organization" className="app-header-nav-link">
              Organization
            </NavLink>
            <NavLink to="/registries" className="app-header-nav-link">
              Registries
            </NavLink>
            {can('audit.read') && (
              <NavLink to="/audit" className="app-header-nav-link">
                Audit log
              </NavLink>
            )}
            {can('users.manage') && (
              <NavLink to="/users" className="app-header-nav-link">
                Users
              </NavLink>
            )}
            {can('users.manage') && (
              <NavLink to="/directory" className="app-header-nav-link">
                Directory
              </NavLink>
            )}
            {can('org.admin') && (
              <NavLink to="/usage" className="app-header-nav-link">
                Usage
              </NavLink>
            )}
            {can('org.admin') && (
              <NavLink to="/data-export" className="app-header-nav-link">
                Data export
              </NavLink>
            )}
            <NavLink to="/sessions" className="app-header-nav-link">
              Sessions
            </NavLink>
          </nav>
          <span className="app-header-user">
            <span className="app-header-identity">
              <span className="app-header-user-name" title={user.name}>{user.name}</span>
              <span className="app-header-roles">
                {user.roles.length === 0 && <span className="badge role-badge">Read only</span>}
                {user.roles.map((grant) => (
                  <span key={`${grant.role}:${grant.scope_id}`} className="badge role-badge">{grantLabel(grant)}</span>
                ))}
              </span>
            </span>
            <button type="button" className="button-secondary" disabled={loggingOut} onClick={handleLogout}>
              {loggingOut ? 'Logging out...' : 'Log out'}
            </button>
            {logoutError && <span role="alert">Could not log out. Please try again.</span>}
          </span>
        </>
      )}
    </header>
  )
}

// Epic 38, US38.8: the hosting company's support is in, under a grant.
function SupportBanner() {
  const { user } = useAuth()
  if (!user?.support) return null
  const expires = user.support.expires_at
  const until = new Date(expires.endsWith('Z') ? expires : `${expires}Z`).toLocaleString()
  return (
    <p className="support-banner" role="note">
      Support session ({user.support.access === 'read' ? 'read-only' : 'can edit'}), granted by {user.support.granted_by},
      until {until}. Everything you do here is in this organization's audit log.
    </p>
  )
}

// Epic 37, US37.14: local sign-in lets anyone be anyone, so every page says so.
function LocalAuthBanner() {
  const [mode, setMode] = useState<string | null>(null)
  useEffect(() => {
    getAuthConfig().then((config) => setMode(config.auth_mode)).catch(() => setMode(null))
  }, [])
  if (mode !== 'local') return null
  return <p className="local-auth-banner" role="note">Local sign-in -- not for production</p>
}

function AppRoutes() {
  const { user, loading } = useAuth()

  if (loading) return <p className="loading-state" role="status">Loading your workspace...</p>
  if (!user) return <LoginPage />

  return (
    <Routes>
      <Route path="/" element={<ProcessListPage />} />
      <Route path="/registries" element={<RegistriesPage />} />
      <Route path="/organization" element={<OrganizationPage />} />
      <Route path="/audit" element={<AuditLogPage />} />
      <Route path="/users" element={<UsersPage />} />
      <Route path="/sessions" element={<SessionsPage />} />
      <Route path="/directory" element={<DirectoryPage />} />
      <Route path="/usage" element={<UsagePage />} />
      <Route path="/data-export" element={<DataExportPage />} />
      <Route path="/processes/:processId" element={<ProcessDetailPage />} />
      <Route path="/processes/:processId/diagram" element={<DiagramPage />} />
      <Route path="/processes/:processId/versions" element={<VersionsPage />} />
      <Route path="/processes/:processId/blueprint" element={<BlueprintPage />} />
      <Route path="/processes/:processId/gaps" element={<GapReviewPage />} />
      <Route path="/processes/:processId/datasets" element={<DatasetsPage />} />
      <Route path="/processes/:processId/batches/:batchId" element={<BatchResultsPage />} />
      <Route path="/processes/:processId/process-twin" element={<ProcessTwinPage />} />
      <Route path="*" element={<NotFoundPage />} />
    </Routes>
  )
}

export default function App() {
  return (
    <AuthProvider>
      <div className="app-shell">
        <a href="#main-content" className="skip-link">Skip to main content</a>
        <AppHeader />
        <SupportBanner />
        <LocalAuthBanner />
        <div className="app">
          <main id="main-content" tabIndex={-1}>
            <ErrorBoundary>
              <Suspense fallback={<p className="loading-state" role="status">Loading workspace...</p>}>
                <AppRoutes />
              </Suspense>
            </ErrorBoundary>
          </main>
        </div>
      </div>
    </AuthProvider>
  )
}
