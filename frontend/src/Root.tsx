import { lazy, Suspense, useEffect, useState } from 'react'
import { BrowserRouter } from 'react-router-dom'
import App from './App.tsx'
import { getHostKind } from './api/client'

// Epic 38, US38.8: the operator console answers on its own host, and is
// loaded only there.
const OperatorConsole = lazy(() => import('./operator/OperatorConsole'))

export default function Root() {
  const [kind, setKind] = useState<'tenant' | 'operator' | null>(null)
  useEffect(() => {
    getHostKind()
      .then((host) => setKind(host.kind))
      .catch(() => setKind('tenant'))
  }, [])
  if (kind === null) return null
  if (kind === 'operator') {
    return (
      <Suspense fallback={null}>
        <OperatorConsole />
      </Suspense>
    )
  }
  return (
    <BrowserRouter>
      <App />
    </BrowserRouter>
  )
}
