import { api, useIpcEvent } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function ScanProgressView(): JSX.Element {
  const {
    scanProgress,
    setScanProgress,
    setPhase,
    setPreviewSummary,
    setNeedsResolutionRecords,
    setCameraGroups
  } = useAppStore()

  useIpcEvent(api().onScanProgress, setScanProgress)
  useIpcEvent(api().onScanCancelled, () => {
    setPhase('pickDirs')
  })
  useIpcEvent(api().onScanNeedsResolution, (e) => {
    setNeedsResolutionRecords(e.records)
    setPhase('resolution')
  })
  useIpcEvent(api().onScanNeedsTimeCorrection, (e) => {
    setCameraGroups(e.groups)
    setPhase('timeCorrection')
  })
  useIpcEvent(api().onScanReadyForPreview, (e) => {
    setPreviewSummary(e)
    setPhase('preview')
  })

  const processed = scanProgress?.processed ?? 0
  const total = scanProgress?.total ?? 0
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0

  return (
    <div>
      <h1>Scanning...</h1>
      <p>
        {processed} / {total} files ({pct}%)
      </p>
      {scanProgress?.currentPath && <p style={{ fontSize: 12, opacity: 0.7 }}>{scanProgress.currentPath}</p>}
      <p>{scanProgress?.skippedNonMedia ?? 0} non-media files skipped</p>
      <button onClick={() => api().scanCancel()}>Cancel</button>
    </div>
  )
}
