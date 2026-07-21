import { randomUUID } from 'crypto'
import { walkDirectory } from '../scanning/fileWalker'
import { isWithinDateRange } from '../scanning/dateRangeFilter'
import { readTags } from '../metadata/exiftoolClient'
import { normalizeMetadata } from '../metadata/metadataNormalizer'
import { geocodeCoords } from '../geocoding/geocodeService'
import { assignSequenceNumbers } from '../naming/sequencer'
import { buildFileName } from '../naming/filenameBuilder'
import { buildDestPath } from '../naming/pathBuilder'
import { resetRunState, setRecords, setSkippedNonMedia, getRunState } from './runState'
import { waitForResolution, cancelResolution } from './resolutionGate'
import { sendToRenderer } from '../ipc/rendererBridge'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type {
  ScanStartRequest,
  ScanProgressEvent,
  ScanNeedsResolutionEvent,
  ScanReadyForPreviewEvent
} from '../../shared/types/IpcContracts'
import type { ScanRecord } from '../../shared/types/ScanRecord'

let cancelled = false

export function cancelScan(): void {
  cancelled = true
  cancelResolution()
}

/**
 * Walk -> extract metadata -> geocode GPS coords -> pause for user resolution of
 * any records missing a reliable date -> sequence -> name/path.
 */
export async function startScan(req: ScanStartRequest): Promise<void> {
  cancelled = false
  resetRunState(req.sourceDir, req.destDir, req.organizeMode)

  try {
    await runScan(req)
  } finally {
    // Every early-return below happens because `cancelled` was set - if we get here
    // with it still true, the renderer is stuck showing scan progress with nothing
    // left to tell it the scan is over, so it never leaves that screen. Send an
    // explicit signal so it can go back to picking directories.
    if (cancelled) {
      sendToRenderer(IpcChannels.scanCancelled, {})
    }
  }
}

async function runScan(req: ScanStartRequest): Promise<void> {
  const { files, skippedNonMedia } = await walkDirectory(req.sourceDir, req.includeSubfolders, {
    includePhotos: req.includePhotos,
    includeVideos: req.includeVideos
  })
  setSkippedNonMedia(skippedNonMedia)

  const total = files.length
  const extracted: ScanRecord[] = []

  for (let i = 0; i < files.length; i++) {
    if (cancelled) return
    const file = files[i]
    const progress: ScanProgressEvent = {
      phase: 'extracting',
      processed: i,
      total,
      currentPath: file.path,
      skippedNonMedia
    }
    sendToRenderer(IpcChannels.scanProgress, progress)

    const result = await readTags(file.path)
    extracted.push(normalizeMetadata(file, result, randomUUID()))
  }

  if (cancelled) return

  const records = extracted.filter((r) => isWithinDateRange(r, req.dateRangeStart, req.dateRangeEnd))
  const excludedByDateRange = extracted.length - records.length

  const withGps = records.filter((r) => r.gps)
  for (let i = 0; i < withGps.length; i++) {
    if (cancelled) return
    const record = withGps[i]
    const progress: ScanProgressEvent = {
      phase: 'geocoding',
      processed: i,
      total: withGps.length,
      currentPath: record.originalPath,
      skippedNonMedia
    }
    sendToRenderer(IpcChannels.scanProgress, progress)

    try {
      const location = await geocodeCoords(record.gps!.lat, record.gps!.lon)
      record.location = location
      record.geocodeStatus = location ? 'ok' : 'notFound'
    } catch {
      record.geocodeStatus = 'error'
    }
  }

  if (cancelled) return

  for (const record of records) {
    if (record.exifCapturedAt) {
      record.effectiveCapturedAt = record.exifCapturedAt
      record.dateSource = 'exif'
      record.needsResolution = false
    }
  }

  const needsResolution = records.filter((r) => r.needsResolution)
  const excludedIds = new Set<string>()
  if (needsResolution.length > 0) {
    const event: ScanNeedsResolutionEvent = { records: needsResolution }
    sendToRenderer(IpcChannels.scanNeedsResolution, event)

    const decisions = await waitForResolution()
    if (cancelled) return

    const decisionById = new Map(decisions.map((d) => [d.id, d]))
    for (const record of needsResolution) {
      const decision = decisionById.get(record.id)
      if (!decision) continue // shouldn't happen - renderer must decide every flagged record

      if (decision.exclude) {
        excludedIds.add(record.id)
        continue
      }

      record.dateSource = decision.dateSource
      record.effectiveCapturedAt =
        decision.dateSource === 'manual' ? decision.manualCapturedAt : record.mtime
      record.destinationOverride = decision.destinationOverride
      record.needsResolution = false
    }
  }

  if (cancelled) return

  const included = excludedIds.size > 0 ? records.filter((r) => !excludedIds.has(r.id)) : records
  const sequenced = assignSequenceNumbers(included)
  const totalCount = sequenced.length
  const finalized = sequenced.map((record) => {
    const fileName = buildFileName(record, totalCount)
    const destPath = buildDestPath(record, req.destDir, fileName, req.organizeMode)
    return { ...record, finalFileName: fileName, finalDestPath: destPath }
  })

  setRecords(finalized)

  const readyEvent: ScanReadyForPreviewEvent = {
    totalCount,
    flaggedCount: finalized.filter((r) => r.dateSource !== 'exif').length,
    errorCount: finalized.filter((r) => r.metadataStatus === 'unreadable').length,
    excludedByDateRange,
    excludedManually: excludedIds.size
  }
  sendToRenderer(IpcChannels.scanReadyForPreview, readyEvent)
}

export function getAllRecords(): ScanRecord[] {
  return getRunState().records
}
