import { Link, Route, Routes } from 'react-router-dom'
import DiagramPage from './pages/DiagramPage'
import ProcessDetailPage from './pages/ProcessDetailPage'
import ProcessListPage from './pages/ProcessListPage'

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

export default function App() {
  return (
    <div className="app">
      <header className="app-header">
        <Link to="/" className="app-title-link">
          <h1>Agentic Solution Generator</h1>
        </Link>
      </header>
      <main>
        <Routes>
          <Route path="/" element={<ProcessListPage />} />
          <Route path="/processes/:processId" element={<ProcessDetailPage />} />
          <Route path="/processes/:processId/diagram" element={<DiagramPage />} />
          <Route path="*" element={<NotFoundPage />} />
        </Routes>
      </main>
    </div>
  )
}
