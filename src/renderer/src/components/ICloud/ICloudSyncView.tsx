import { api, useIpcEvent } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function ICloudSyncView(): JSX.Element {
  const {
    icloudSyncStatus,
    setIcloudSyncStatus,
    setSourceDir,
    setPhase,
    destDir,
    organizeMode,
    includePhotos,
    includeVideos,
    dateRangeStart,
    dateRangeEnd
  } = useAppStore()

  const needsDestDir =
    organizeMode === 'moveToFolders' || organizeMode === 'copyToFolders' || organizeMode === 'fixTimeOrder'

  async function startScanAfterSync(syncDir: string): Promise<void> {
    setPhase('scanning')
    await api().scanStart({
      sourceDir: syncDir,
      destDir: needsDestDir ? destDir : undefined,
      // icloudpd always downloads flat (--folder-structure none), so subfolder
      // recursion doesn't apply here the way it does for a hand-picked local folder.
      includeSubfolders: true,
      organizeMode,
      includePhotos,
      includeVideos,
      dateRangeStart: dateRangeStart || undefined,
      dateRangeEnd: dateRangeEnd || undefined
    })
  }

  useIpcEvent(api().onIcloudSyncStatus, (status) => {
    setIcloudSyncStatus(status)
    if (status.phase === 'done' && status.syncDir) {
      setSourceDir(status.syncDir)
      void startScanAfterSync(status.syncDir)
    }
    if (status.phase === 'cancelled') {
      setPhase('pickDirs')
    }
  })

  async function cancel(): Promise<void> {
    await api().icloudSyncCancel()
  }

  const phase = icloudSyncStatus?.phase ?? 'starting'

  return (
    <div>
      <h1>Syncing from iCloud...</h1>

      {phase === 'starting' && <p>Starting icloudpd and checking for an existing sign-in session...</p>}

      {phase === 'awaitingAuth' && (
        <>
          <p>
            A sign-in page opened in your default browser. Enter your Apple ID password (and 2FA code if
            asked) there - this app never sees them directly.
          </p>
          <p style={{ fontSize: 12, opacity: 0.7 }}>
            Didn&apos;t open? Visit http://127.0.0.1:8080 in your browser.
          </p>
        </>
      )}

      {(phase === 'authenticated' || phase === 'syncing') && (
        <p>
          Signed in. Downloading photos/videos...{' '}
          {icloudSyncStatus?.downloadedCount !== undefined && (
            <strong>{icloudSyncStatus.downloadedCount} so far</strong>
          )}
        </p>
      )}

      {phase === 'error' && (
        <>
          <p style={{ color: '#c33' }}>Something went wrong. Last output before it stopped:</p>
          <pre
            style={{
              fontSize: 11,
              opacity: 0.8,
              whiteSpace: 'pre-wrap',
              background: 'rgba(128,128,128,0.1)',
              padding: 8,
              maxHeight: 200,
              overflow: 'auto'
            }}
          >
            {(icloudSyncStatus?.error ?? 'unknown error').split(' | ').join('\n')}
          </pre>
        </>
      )}

      {icloudSyncStatus?.logPath && (
        <p style={{ fontSize: 12, opacity: 0.6 }}>Full log: {icloudSyncStatus.logPath}</p>
      )}

      {icloudSyncStatus?.message && phase !== 'error' && (
        <p style={{ fontSize: 12, opacity: 0.6 }}>{icloudSyncStatus.message}</p>
      )}

      <div style={{ marginTop: 16, display: 'flex', gap: 8 }}>
        {phase === 'error' ? (
          <button onClick={() => setPhase('pickDirs')}>Back</button>
        ) : (
          <button onClick={cancel}>Cancel</button>
        )}
      </div>
    </div>
  )
}
