import { useAppStore } from '../../state/appStore'
import { ResultsErrorList } from './ResultsErrorList'

export function ResultsSummary(): JSX.Element {
  const { executeResult, setPhase } = useAppStore()

  if (!executeResult) return <p>No results.</p>

  return (
    <div>
      <h1>Done</h1>
      <p>
        <strong>{executeResult.movedCount}</strong> moved &middot;{' '}
        <strong>{executeResult.failedCount}</strong> failed &middot;{' '}
        <strong>{executeResult.skippedCount}</strong> skipped
      </p>
      {executeResult.operationsLogPath && (
        <p style={{ fontSize: 12, opacity: 0.7 }}>Log: {executeResult.operationsLogPath}</p>
      )}

      <ResultsErrorList results={executeResult.results} />

      <button style={{ marginTop: 16 }} onClick={() => setPhase('pickDirs')}>
        Start another batch
      </button>
    </div>
  )
}
