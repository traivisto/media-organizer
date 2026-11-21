import { useState } from 'react'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'
import { ResultsErrorList } from './ResultsErrorList'
import { destinationFolders } from '../../../../shared/destinationFolders'

export function ResultsSummary(): JSX.Element {
  const { executeResult, setPhase, setArchiveUpdateStatus } = useAppStore()
  const [openError, setOpenError] = useState<string>()

  if (!executeResult) return <p>No results.</p>

  const folders = destinationFolders(executeResult.results)

  async function open(dir: string): Promise<void> {
    setOpenError(await api().openFolder(dir))
  }

  function updateArchive(): void {
    setArchiveUpdateStatus(undefined)
    setPhase('archiveUpdate')
  }

  return (
    <div>
      <h1>Done</h1>
      <p>
        {executeResult.movedCount > 0 && (
          <>
            <strong>{executeResult.movedCount}</strong> moved &middot;{' '}
          </>
        )}
        {executeResult.copiedCount > 0 && (
          <>
            <strong>{executeResult.copiedCount}</strong> copied &middot;{' '}
          </>
        )}
        <strong>{executeResult.failedCount}</strong> failed &middot;{' '}
        <strong>{executeResult.skippedCount}</strong> skipped
      </p>
      {executeResult.operationsLogPath && (
        <p style={{ fontSize: 12, opacity: 0.7 }}>Log: {executeResult.operationsLogPath}</p>
      )}

      <ResultsErrorList results={executeResult.results} />

      {folders.length > 0 && (
        <div style={{ marginTop: 16 }}>
          <h2>Next: look through the photos</h2>
          <p style={{ fontSize: 12, opacity: 0.7 }}>
            Open the folders, delete what you don&apos;t want, then update the photo archive. It
            can also be done later from the start screen.
          </p>
          <ul style={{ paddingLeft: 20 }}>
            {folders.map((dir) => (
              <li key={dir} style={{ marginBottom: 4 }}>
                <button onClick={() => open(dir)}>Open</button> {dir}
              </li>
            ))}
          </ul>
          {openError && <p style={{ color: '#c33' }}>{openError}</p>}
        </div>
      )}

      <div style={{ marginTop: 16 }}>
        {folders.length > 0 && (
          <button onClick={updateArchive} style={{ marginRight: 8 }}>
            Update photo archive...
          </button>
        )}
        <button onClick={() => setPhase('pickDirs')}>Start another batch</button>
      </div>
    </div>
  )
}
