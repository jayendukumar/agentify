import { lazy, Suspense, useState } from 'react'
import { Link, NavLink, Route, Routes, useLocation } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import LoginPage from './auth/LoginPage'
import BrandLogo from './components/BrandLogo'
import ErrorBoundary from './components/ErrorBoundary'
import GapReviewPage from './pages/GapReviewPage'
import ProcessDetailPage from './pages/ProcessDetailPage'
import ProcessListPage from './pages/ProcessListPage'
import RegistriesPage from './pages/RegistriesPage'
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
  const { user, logout } = useAuth()
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
            <NavLink to="/registries" className="app-header-nav-link">
              Registries
            </NavLink>
          </nav>
          <span className="app-header-user">
            <span className="app-header-user-name">
              {user.name} <span className="badge role-badge">{user.role}</span>
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

function AppRoutes() {
  const { user, loading } = useAuth()

  if (loading) return <p className="loading-state" role="status">Loading your workspace...</p>
  if (!user) return <LoginPage />

  return (
    <Routes>
      <Route path="/" element={<ProcessListPage />} />
      <Route path="/registries" element={<RegistriesPage />} />
      <Route path="/processes/:processId" element={<ProcessDetailPage />} />
      <Route path="/processes/:processId/diagram" element={<DiagramPage />} />
      <Route path="/processes/:processId/versions" element={<VersionsPage />} />
      <Route path="/processes/:processId/blueprint" element={<BlueprintPage />} />
      <Route path="/processes/:processId/gaps" element={<GapReviewPage />} />
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
