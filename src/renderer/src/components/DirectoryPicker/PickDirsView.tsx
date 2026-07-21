import { useEffect, useState } from 'react'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function PickDirsView(): JSX.Element {
  const {
    sourceDir,
    destDir,
    organizeMode,
    setSourceDir,
    setDestDir,
    setOrganizeMode,
    setWindowsDateTimeFormat,
    setPhase
  } = useAppStore()
  const [includeSubfolders, setIncludeSubfolders] = useState(true)
  const [includePhotos, setIncludePhotos] = useState(true)
  const [includeVideos, setIncludeVideos] = useState(true)
  const [dateRangeStart, setDateRangeStart] = useState('')
  const [dateRangeEnd, setDateRangeEnd] = useState('')
  const [appleId, setAppleId] = useState('')

  useEffect(() => {
    api()
      .getConfig()
      .then((config) => {
        if (config.lastSourceDir) setSourceDir(config.lastSourceDir)
        if (config.lastDestDir) setDestDir(config.lastDestDir)
        if (config.lastAppleId) setAppleId(config.lastAppleId)
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

  async function syncFromIcloud(): Promise<void> {
    if (!appleId.trim() || !hasMediaTypeSelected) return
    setPhase('icloudSync')
    await api().icloudSyncStart({
      appleId: appleId.trim(),
      includePhotos,
      includeVideos,
      dateRangeStart: dateRangeStart || undefined,
      dateRangeEnd: dateRangeEnd || undefined
    })
  }

  const needsDestDir = organizeMode === 'moveToFolders' || organizeMode === 'copyToFolders'
  const hasMediaTypeSelected = includePhotos || includeVideos
  const canScan = !!sourceDir && (!needsDestDir || !!destDir) && hasMediaTypeSelected

  async function startScan(): Promise<void> {
    if (!canScan || !sourceDir) return
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
      <p>Pick a source folder to scan, then choose what to do with the files.</p>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>Source:</strong> {sourceDir ?? <em>not selected</em>}
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
      </div>

      <div style={{ marginBottom: 16 }}>
        <div style={{ marginBottom: 8 }}>
          <strong>Or, sync directly from iCloud</strong>{' '}
          <span style={{ fontSize: 12, opacity: 0.7 }}>
            (downloads into a local folder, which becomes the source above)
          </span>
        </div>
        <input
          type="email"
          placeholder="Apple ID email"
          value={appleId}
          onChange={(e) => setAppleId(e.target.value)}
          style={{ marginRight: 8 }}
        />
        <button disabled={!appleId.trim() || !hasMediaTypeSelected} onClick={syncFromIcloud}>
          Sync from iCloud
        </button>
        <div style={{ fontSize: 12, opacity: 0.7, marginTop: 4 }}>
          Uses the Photos/Videos and date-range filters below. Your Apple ID password and 2FA code are
          entered on icloudpd&apos;s own sign-in page in your browser - this app never sees them.
        </div>
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
