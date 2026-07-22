import { useEffect } from 'react'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function PickDirsView(): JSX.Element {
  const {
    sourceDir,
    destDir,
    organizeMode,
    sourceType,
    includeSubfolders,
    includePhotos,
    includeVideos,
    dateRangeStart,
    dateRangeEnd,
    appleId,
    setSourceDir,
    setDestDir,
    setOrganizeMode,
    setWindowsDateTimeFormat,
    setPhase,
    setSourceType,
    setIncludeSubfolders,
    setIncludePhotos,
    setIncludeVideos,
    setDateRangeStart,
    setDateRangeEnd,
    setAppleId
  } = useAppStore()

  useEffect(() => {
    api()
      .getConfig()
      .then((config) => {
        if (config.lastSourceDir) setSourceDir(config.lastSourceDir)
        if (config.lastDestDir) setDestDir(config.lastDestDir)
        if (config.lastAppleId) setAppleId(config.lastAppleId)
        if (config.lastSourceType) setSourceType(config.lastSourceType)
        setWindowsDateTimeFormat(config.windowsDatePattern, config.windowsTimePattern)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  async function pickSource(): Promise<void> {
    const dir = await api().pickSourceDir()
    if (dir) setSourceDir(dir)
  }

  async function pickDest(): Promise<void> {
    const dir = await api().pickDestDir()
    if (dir) setDestDir(dir)
  }

  const needsDestDir = organizeMode === 'moveToFolders' || organizeMode === 'copyToFolders'
  const hasMediaTypeSelected = includePhotos || includeVideos
  const hasValidSource = sourceType === 'local' ? !!sourceDir : !!appleId.trim()
  const canScan = hasValidSource && (!needsDestDir || !!destDir) && hasMediaTypeSelected

  async function startScan(): Promise<void> {
    if (!canScan) return

    if (sourceType === 'icloud') {
      setPhase('icloudSync')
      await api().icloudSyncStart({
        appleId: appleId.trim(),
        includePhotos,
        includeVideos,
        dateRangeStart: dateRangeStart || undefined,
        dateRangeEnd: dateRangeEnd || undefined
      })
      return
    }

    if (!sourceDir) return
    setPhase('scanning')
    await api().scanStart({
      sourceDir,
      destDir: needsDestDir ? destDir : undefined,
      includeSubfolders,
      organizeMode,
      includePhotos,
      includeVideos,
      dateRangeStart: dateRangeStart || undefined,
      dateRangeEnd: dateRangeEnd || undefined
    })
  }

  return (
    <div>
      <h1>Media Organizer</h1>
      <p>Pick a source, then choose what to do with the files.</p>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>Source</strong>
        </div>
        <label style={{ display: 'block', marginBottom: 4 }}>
          <input
            type="radio"
            checked={sourceType === 'local'}
            onChange={() => setSourceType('local')}
          />{' '}
          Local folder
        </label>
        <label style={{ display: 'block', marginBottom: 8 }}>
          <input
            type="radio"
            checked={sourceType === 'icloud'}
            onChange={() => setSourceType('icloud')}
          />{' '}
          iCloud
        </label>

        {sourceType === 'local' ? (
          <>
            <div style={{ marginBottom: 8 }}>
              <strong>Folder:</strong> {sourceDir ?? <em>not selected</em>}
            </div>
            <button onClick={pickSource}>Choose source folder</button>
            <div style={{ marginTop: 8 }}>
              <label>
                <input
                  type="checkbox"
                  checked={includeSubfolders}
                  onChange={(e) => setIncludeSubfolders(e.target.checked)}
                />{' '}
                Include subfolders
              </label>
            </div>
          </>
        ) : (
          <>
            <input
              type="email"
              placeholder="Apple ID email"
              value={appleId}
              onChange={(e) => setAppleId(e.target.value)}
            />
            <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
              Uses the Photos/Videos and date-range filters below. Your Apple ID password and 2FA
              code are entered on icloudpd&apos;s own sign-in page in your browser - this app
              never sees them. The sync starts when you press Scan below.
            </div>
            {sourceDir && (
              <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
                Last synced into: {sourceDir}
              </div>
            )}
          </>
        )}
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>Filters</strong> <span style={{ fontSize: 12, opacity: 0.7 }}>(optional - default is all files)</span>
        </div>
        <div style={{ marginBottom: 8 }}>
          <label style={{ marginRight: 16 }}>
            <input type="checkbox" checked={includePhotos} onChange={(e) => setIncludePhotos(e.target.checked)} />{' '}
            Photos
          </label>
          <label>
            <input type="checkbox" checked={includeVideos} onChange={(e) => setIncludeVideos(e.target.checked)} />{' '}
            Videos
          </label>
          {!hasMediaTypeSelected && (
            <span style={{ color: '#c33', marginLeft: 12, fontSize: 12 }}>
              Select at least one of Photos/Videos.
            </span>
          )}
        </div>
        <div>
          <label style={{ marginRight: 8 }}>
            From{' '}
            <input type="date" value={dateRangeStart} onChange={(e) => setDateRangeStart(e.target.value)} />
          </label>
          <label>
            To <input type="date" value={dateRangeEnd} onChange={(e) => setDateRangeEnd(e.target.value)} />
          </label>
          {(dateRangeStart || dateRangeEnd) && (
            <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
              Files are pre-filtered by their file-system date before their full metadata is read, so a
              narrow range scans much faster in large folders.
            </div>
          )}
        </div>
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>What should happen to the files?</strong>
        </div>
        <label style={{ display: 'block', marginBottom: 4 }}>
          <input
            type="radio"
            checked={organizeMode === 'moveToFolders'}
            onChange={() => setOrganizeMode('moveToFolders')}
          />{' '}
          Rename and move into Year/Month folders in a destination
        </label>
        <label style={{ display: 'block', marginBottom: 4 }}>
          <input
            type="radio"
            checked={organizeMode === 'renameInPlace'}
            onChange={() => setOrganizeMode('renameInPlace')}
          />{' '}
          Rename only, keep files in their current folder
        </label>
        <label style={{ display: 'block' }}>
          <input
            type="radio"
            checked={organizeMode === 'copyToFolders'}
            onChange={() => setOrganizeMode('copyToFolders')}
          />{' '}
          Rename and copy into Year/Month folders in a destination, keeping the originals in place
        </label>
        {sourceType === 'icloud' && (
          <div style={{ fontSize: 12, opacity: 0.7, marginTop: 8 }}>
            Since the source is from iCloud sync, these actions only affect the downloaded local
            copy - nothing is ever deleted from iCloud itself, regardless of which option you
            choose.
          </div>
        )}
      </div>

      {needsDestDir && (
        <div style={{ marginBottom: 16 }}>
          <div style={{ marginBottom: 8 }}>
            <strong>Destination:</strong> {destDir ?? <em>not selected</em>}
          </div>
          <button onClick={pickDest}>Choose destination folder</button>
        </div>
      )}

      <button disabled={!canScan} onClick={startScan}>
        Scan
      </button>
    </div>
  )
}
