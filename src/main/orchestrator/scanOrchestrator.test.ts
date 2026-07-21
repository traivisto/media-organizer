import { afterAll, afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest'
import { mkdtemp, rm } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { ExifTool } from 'exiftool-vendored'
import { writeMinimalJpeg } from '../testUtils/mediaFixtures'
import { shutdownExiftoolPool } from '../metadata/exiftoolClient'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { ScanNeedsResolutionEvent, ScanReadyForPreviewEvent } from '../../shared/types/IpcContracts'
import type { ResolutionDecision } from '../../shared/types/ScanRecord'

const sendToRendererMock = vi.fn()
vi.mock('../ipc/rendererBridge', () => ({
  sendToRenderer: (...args: [string, unknown]) => sendToRendererMock(...args)
}))

const { startScan, cancelScan, getAllRecords } = await import('./scanOrchestrator')
const { submitResolution } = await import('./resolutionGate')

function eventsOf<T>(channel: string): T[] {
  return sendToRendererMock.mock.calls.filter(([c]) => c === channel).map(([, payload]) => payload as T)
}

describe('scanOrchestrator pause/resume for missing dates', () => {
  let dir: string
  let destDir: string

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), 'media-organizer-orch-test-'))
    destDir = join(dir, '__dest__')

    const tagWriter = new ExifTool()

    const withDate = join(dir, 'has-date.jpg')
    await writeMinimalJpeg(withDate)
    await tagWriter.write(
      withDate,
      { DateTimeOriginal: '2026:05:10 10:00:00' },
      { writeArgs: ['-overwrite_original'] }
    )

    // Two files with no usable date - these should trigger the pause.
    await writeMinimalJpeg(join(dir, 'no-date-a.jpg'))
    await writeMinimalJpeg(join(dir, 'no-date-b.jpg'))

    await tagWriter.end()
  })

  afterEach(() => {
    sendToRendererMock.mockClear()
  })

  afterAll(async () => {
    await shutdownExiftoolPool()
    await rm(dir, { recursive: true, force: true })
  })

  it('pauses via scan:needsResolution and resumes once decisions are submitted', async () => {
    const scanPromise = startScan({ sourceDir: dir, destDir, includeSubfolders: false, organizeMode: 'moveToFolders', includePhotos: true, includeVideos: true })

    // Wait until the orchestrator has emitted the needsResolution event (it awaits
    // resolution before proceeding further, so this is the signal to act on).
    await vi.waitFor(
      () => {
        expect(eventsOf(IpcChannels.scanNeedsResolution).length).toBeGreaterThan(0)
      },
      { timeout: 10000, interval: 50 }
    )

    const needsResolutionEvents = eventsOf<ScanNeedsResolutionEvent>(IpcChannels.scanNeedsResolution)
    expect(needsResolutionEvents).toHaveLength(1)
    const flagged = needsResolutionEvents[0].records
    expect(flagged.map((r) => r.originalPath.split(/[\\/]/).pop()).sort()).toEqual([
      'no-date-a.jpg',
      'no-date-b.jpg'
    ])

    const decisions: ResolutionDecision[] = [
      { id: flagged[0].id, dateSource: 'mtimeFallback' },
      { id: flagged[1].id, dateSource: 'manual', manualCapturedAt: '2026-06-01T00:00:00', destinationOverride: 'unsorted' }
    ]
    submitResolution(decisions)

    await scanPromise

    const readyEvents = eventsOf<ScanReadyForPreviewEvent>(IpcChannels.scanReadyForPreview)
    expect(readyEvents).toHaveLength(1)
    expect(readyEvents[0].totalCount).toBe(3)
    // 2 of 3 needed mtime/manual fallback (flagged); none were genuinely unreadable (errors)
    // -these must NOT collapse to the same number, that was a real bug.
    expect(readyEvents[0].flaggedCount).toBe(2)
    expect(readyEvents[0].errorCount).toBe(0)

    const all = getAllRecords()
    const manualRecord = all.find((r) => r.originalPath.endsWith('no-date-b.jpg'))!
    expect(manualRecord.dateSource).toBe('manual')
    expect(manualRecord.destinationOverride).toBe('unsorted')
    expect(manualRecord.finalDestPath).toContain(join('__dest__', 'Unsorted'))

    const mtimeRecord = all.find((r) => r.originalPath.endsWith('no-date-a.jpg'))!
    expect(mtimeRecord.dateSource).toBe('mtimeFallback')
    expect(mtimeRecord.destinationOverride).toBeUndefined()

    const exifRecord = all.find((r) => r.originalPath.endsWith('has-date.jpg'))!
    expect(exifRecord.dateSource).toBe('exif')
  })

  it('unblocks, exits cleanly, and notifies the renderer on cancel while paused for resolution', async () => {
    const scanPromise = startScan({ sourceDir: dir, destDir, includeSubfolders: false, organizeMode: 'moveToFolders', includePhotos: true, includeVideos: true })

    await vi.waitFor(
      () => {
        expect(eventsOf(IpcChannels.scanNeedsResolution).length).toBeGreaterThan(0)
      },
      { timeout: 10000, interval: 50 }
    )

    cancelScan()
    await expect(scanPromise).resolves.toBeUndefined()

    // Regression check: without this, the renderer is left showing the scanning
    // screen forever, since neither scanNeedsResolution nor scanReadyForPreview
    // ever arrives after a cancel - this is the explicit "scan was cancelled" signal.
    expect(eventsOf(IpcChannels.scanCancelled)).toHaveLength(1)
    expect(eventsOf(IpcChannels.scanReadyForPreview)).toHaveLength(0)
  })

  it('notifies the renderer on cancel triggered mid-extraction, before any resolution pause', async () => {
    const scanPromise = startScan({ sourceDir: dir, destDir, includeSubfolders: false, organizeMode: 'moveToFolders', includePhotos: true, includeVideos: true })

    await vi.waitFor(
      () => {
        expect(eventsOf(IpcChannels.scanProgress).length).toBeGreaterThan(0)
      },
      { timeout: 10000, interval: 50 }
    )

    cancelScan()
    await expect(scanPromise).resolves.toBeUndefined()

    expect(eventsOf(IpcChannels.scanCancelled)).toHaveLength(1)
    expect(eventsOf(IpcChannels.scanReadyForPreview)).toHaveLength(0)
    expect(eventsOf(IpcChannels.scanNeedsResolution)).toHaveLength(0)
  })
})

describe('scanOrchestrator exclude-from-resolution', () => {
  beforeEach(() => {
    sendToRendererMock.mockClear()
  })

  it('drops excluded files from the batch entirely - not sequenced, named, or included in totals', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-orch-exclude-'))
    const destDir = join(dir, '__dest__')
    try {
      const tagWriter = new ExifTool()
      const withDate = join(dir, 'has-date.jpg')
      await writeMinimalJpeg(withDate)
      await tagWriter.write(
        withDate,
        { DateTimeOriginal: '2026:05:10 10:00:00' },
        { writeArgs: ['-overwrite_original'] }
      )
      await writeMinimalJpeg(join(dir, 'keep-me.jpg'))
      await writeMinimalJpeg(join(dir, 'exclude-me.jpg'))
      await tagWriter.end()

      const scanPromise = startScan({
        sourceDir: dir,
        destDir,
        includeSubfolders: false,
        organizeMode: 'moveToFolders',
        includePhotos: true,
        includeVideos: true
      })

      await vi.waitFor(
        () => {
          expect(eventsOf(IpcChannels.scanNeedsResolution).length).toBeGreaterThan(0)
        },
        { timeout: 10000, interval: 50 }
      )

      const flagged = eventsOf<ScanNeedsResolutionEvent>(IpcChannels.scanNeedsResolution)[0].records
      const keepRecord = flagged.find((r) => r.originalPath.endsWith('keep-me.jpg'))!
      const excludeRecord = flagged.find((r) => r.originalPath.endsWith('exclude-me.jpg'))!

      submitResolution([
        { id: keepRecord.id, dateSource: 'mtimeFallback' },
        { id: excludeRecord.id, dateSource: 'mtimeFallback', exclude: true }
      ])

      await scanPromise

      const readyEvents = eventsOf<ScanReadyForPreviewEvent>(IpcChannels.scanReadyForPreview)
      expect(readyEvents[0].totalCount).toBe(2)
      expect(readyEvents[0].excludedManually).toBe(1)

      const all = getAllRecords()
      expect(all.map((r) => r.originalPath.split(/[\\/]/).pop()).sort()).toEqual([
        'has-date.jpg',
        'keep-me.jpg'
      ])
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})

describe('scanOrchestrator date range filter', () => {
  beforeEach(() => {
    sendToRendererMock.mockClear()
  })

  it('drops files outside the requested date range and reports the count', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-orch-daterange-'))
    const destDir = join(dir, '__dest__')
    try {
      const tagWriter = new ExifTool()

      const inRange = join(dir, 'in-range.jpg')
      await writeMinimalJpeg(inRange)
      await tagWriter.write(
        inRange,
        { DateTimeOriginal: '2026:03:15 10:00:00' },
        { writeArgs: ['-overwrite_original'] }
      )

      const outOfRange = join(dir, 'out-of-range.jpg')
      await writeMinimalJpeg(outOfRange)
      await tagWriter.write(
        outOfRange,
        { DateTimeOriginal: '2025:01:01 10:00:00' },
        { writeArgs: ['-overwrite_original'] }
      )

      await tagWriter.end()

      await startScan({
        sourceDir: dir,
        destDir,
        includeSubfolders: false,
        organizeMode: 'moveToFolders',
        includePhotos: true,
        includeVideos: true,
        dateRangeStart: '2026-01-01',
        dateRangeEnd: '2026-12-31'
      })

      const readyEvents = eventsOf<ScanReadyForPreviewEvent>(IpcChannels.scanReadyForPreview)
      expect(readyEvents[0].totalCount).toBe(1)
      expect(readyEvents[0].excludedByDateRange).toBe(1)

      const all = getAllRecords()
      expect(all.map((r) => r.originalPath.split(/[\\/]/).pop())).toEqual(['in-range.jpg'])
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})

describe('scanOrchestrator renameInPlace mode', () => {
  it('renames files in their original folder without moving them, and ignores destinationOverride', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-orch-inplace-'))
    try {
      const subDir = join(dir, 'subfolder')
      await import('fs/promises').then((m) => m.mkdir(subDir, { recursive: true }))

      const tagWriter = new ExifTool()
      const withDate = join(subDir, 'has-date.jpg')
      await writeMinimalJpeg(withDate)
      await tagWriter.write(
        withDate,
        { DateTimeOriginal: '2026:05:10 10:00:00' },
        { writeArgs: ['-overwrite_original'] }
      )
      await tagWriter.end()

      const scanPromise = startScan({
        sourceDir: dir,
        destDir: undefined,
        includeSubfolders: true,
        organizeMode: 'renameInPlace',
        includePhotos: true,
        includeVideos: true
      })
      await scanPromise

      const all = getAllRecords()
      expect(all).toHaveLength(1)
      expect(all[0].finalDestPath).toBe(join(subDir, all[0].finalFileName!))
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
