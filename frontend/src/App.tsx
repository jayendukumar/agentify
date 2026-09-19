import { Link, Route, Routes } from 'react-router-dom'
import { AuthProvider, useAuth } from './auth/AuthContext'
import LoginPage from './auth/LoginPage'
import ErrorBoundary from './components/ErrorBoundary'
import BlueprintPage from './pages/BlueprintPage'
import DiagramPage from './pages/DiagramPage'
import GapReviewPage from './pages/GapReviewPage'
import ProcessDetailPage from './pages/ProcessDetailPage'
import ProcessListPage from './pages/ProcessListPage'
import VersionsPage from './pages/VersionsPage'

function NotFoundPage() {
  return (
    <div className="page">
      <p>That page doesn't exist.</p>
      <p>
        <Link to="/">&larr; All processes</Link>
      </p>
    </div>
  )
}

function AppHeader() {
  const { user, logout } = useAuth()
  return (
    <header className="app-header">
      <Link to="/" className="app-title-link">
        <h1>Agentic Solution Generator</h1>
      </Link>
      {user && (
        <span className="app-header-user">
          <span className="meta">
            {user.name} ({user.role})
          </span>
          <button type="button" onClick={() => void logout()}>
            Log out
          </button>
        </span>
      )}
    </header>
  )
}

function AppRoutes() {
  const { user, loading } = useAuth()

  if (loading) return <p>Loading...</p>
  if (!user) return <LoginPage />

  return (
    <Routes>
      <Route path="/" element={<ProcessListPage />} />
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
      <div className="app">
        <AppHeader />
        <main>
          <ErrorBoundary>
            <AppRoutes />
          </ErrorBoundary>
        </main>
      </div>
    </AuthProvider>
  )
}
