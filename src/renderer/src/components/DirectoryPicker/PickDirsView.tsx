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

  useEffect(() => {
    api()
      .getConfig()
      .then((config) => {
        if (config.lastSourceDir) setSourceDir(config.lastSourceDir)
        if (config.lastDestDir) setDestDir(config.lastDestDir)
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

  const needsDestDir = organizeMode === 'moveToFolders'
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
        <label style={{ display: 'block' }}>
          <input
            type="radio"
            checked={organizeMode === 'renameInPlace'}
            onChange={() => setOrganizeMode('renameInPlace')}
          />{' '}
          Rename only, keep files in their current folder
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
