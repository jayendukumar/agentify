import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
}

// US10.2: the one gap in this app's otherwise-consistent error handling
// (every page already catches ApiError and shows a message) -- an
// uncaught render exception has nothing to catch it and shows a blank
// white screen instead. A class component is still the only way React
// lets you catch render errors (no hook equivalent).
export default class ErrorBoundary extends Component<Props, State> {
  state: State = { hasError: false }

  static getDerivedStateFromError(): State {
    return { hasError: true }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('Unhandled render error', error, info.componentStack)
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="page">
          <p className="error">Something went wrong displaying this page.</p>
          <button type="button" onClick={() => window.location.reload()}>
            Reload
          </button>
        </div>
      )
    }
    return this.props.children
  }
}
