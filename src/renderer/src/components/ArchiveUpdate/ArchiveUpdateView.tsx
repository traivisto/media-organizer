import { api, useIpcEvent } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'
import type { ArchiveUpdateStage } from '../../../../shared/types/IpcContracts'

const LABELS: Record<ArchiveUpdateStage, string> = {
  scan: 'Find new and removed photos',
  embed: 'Search vectors and thumbnails',
  faces: 'Detect faces',
  cluster: 'Match faces to people',
  derive: 'Places, events and questions',
  export: 'Back up names (names.json)',
  publish: 'Publish to the NAS'
}
const ALL_STAGES = Object.keys(LABELS) as ArchiveUpdateStage[]

export function ArchiveUpdateView(): JSX.Element {
  const { archiveUpdateStatus: status, setArchiveUpdateStatus, setPhase } = useAppStore()

  useIpcEvent(api().onArchiveUpdateStatus, setArchiveUpdateStatus)

  const running = status?.state === 'running'
  // The run says which stages it has (export and publish depend on its config).
  const shown = status?.plan ?? ALL_STAGES
  const current = status?.stage ? shown.indexOf(status.stage) + 1 : 0

  function start(): void {
    setArchiveUpdateStatus({ state: 'running' })
    void api().archiveUpdateStart()
  }

  function back(): void {
    setArchiveUpdateStatus(undefined)
    setPhase('pickDirs')
  }

  return (
    <div>
      <h1>Update photo archive</h1>
      {!status && (
        <>
          <p>
            Adds the photos now in the archive folders to the photo archive: finds new and
            removed files, builds search vectors and thumbnails, detects faces and matches them to
            people already named, works out places and events, backs up the names, and publishes
            the result to the NAS viewer. Run it after naming faces too.
          </p>
          <p style={{ fontSize: 12, opacity: 0.7 }}>
            Look through the new photos first and delete any you don&apos;t want - what is in
            the folders when you start is what gets indexed. Photos deleted later are noticed by
            the next update.
          </p>
          <button onClick={start}>Start update</button>{' '}
          <button onClick={back}>Back</button>
        </>
      )}

      {status && (
        <>
          <ol style={{ paddingLeft: 20 }}>
            {shown.map((s, i) => {
              const n = i + 1
              const doneStage = status.state === 'done' || n < current
              const active = running && n === current
              const failedHere = status.state === 'error' && n === current
              const mark = doneStage ? '✓' : failedHere ? '✗' : active ? '…' : ''
              return (
                <li
                  key={s}
                  style={{ opacity: doneStage || active || failedHere ? 1 : 0.5, marginBottom: 2 }}
                >
                  {LABELS[s]} <strong>{mark}</strong>
                </li>
              )
            })}
          </ol>

          {running && status.detail && (
            <p
              style={{
                fontSize: 12,
                opacity: 0.7,
                fontFamily: 'monospace',
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis'
              }}
              title={status.detail}
            >
              {status.detail}
            </p>
          )}

          {status.addedRoots && status.addedRoots.length > 0 && (
            <p>
              New year folder, indexed from now on: <strong>{status.addedRoots.join(', ')}</strong>
            </p>
          )}

          {status.state === 'done' && status.summary && (
            <div>
              <h2>Done</h2>
              <p>
                <strong>{status.summary.newPhotographs}</strong> new photos &middot;{' '}
                <strong>{status.summary.newFaces}</strong> new faces, of which{' '}
                <strong>{status.summary.newlyNamedFaces}</strong> recognised
                {status.summary.missingFiles > 0 && (
                  <>
                    {' '}
                    &middot; <strong>{status.summary.missingFiles}</strong> files no longer there
                  </>
                )}
              </p>
              <p>
                {status.summary.published
                  ? 'Published to the NAS: the viewer shows the new photos.'
                  : 'Not published to the NAS (the photo archive has no publish_to set).'}
              </p>
            </div>
          )}

          {status.state === 'error' && (
            <div>
              <h2>Stopped</h2>
              <p style={{ color: '#c33' }}>{status.error}</p>
              <p style={{ fontSize: 12, opacity: 0.7 }}>
                Whatever finished before this is kept; starting the update again carries on from
                there.
              </p>
            </div>
          )}

          {status.state === 'cancelled' && (
            <p>
              Cancelled. Whatever finished before this is kept; starting the update again carries
              on from there.
            </p>
          )}

          {status.logPath && !running && (
            <p style={{ fontSize: 12, opacity: 0.7 }}>Log: {status.logPath}</p>
          )}

          {running ? (
            <button onClick={() => api().archiveUpdateCancel()}>Cancel</button>
          ) : (
            <>
              {status.state !== 'done' && <button onClick={start}>Try again</button>}{' '}
              <button onClick={back}>Back to start</button>
            </>
          )}
        </>
      )}
    </div>
  )
}
