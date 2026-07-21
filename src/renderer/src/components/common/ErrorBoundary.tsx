import { Component, type ErrorInfo, type ReactNode } from 'react'

interface Props {
  children: ReactNode
}

interface State {
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = {}

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error('Unhandled error in renderer:', error, info.componentStack)
  }

  render(): ReactNode {
    if (this.state.error) {
      return (
        <div style={{ padding: 24 }}>
          <h1>Something went wrong</h1>
          <p>{this.state.error.message}</p>
          <button onClick={() => this.setState({ error: undefined })}>Try again</button>
        </div>
      )
    }
    return this.props.children
  }
}
