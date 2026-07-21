import { describe, expect, it } from 'vitest'
import { sep } from 'path'
import { buildDestPath } from './pathBuilder'
import type { ScanRecord } from '../../shared/types/ScanRecord'

function baseRecord(overrides: Partial<ScanRecord> = {}): ScanRecord {
  return {
    id: '1',
    originalPath: 'IMG_0001.jpg',
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 100,
    mtime: '2026-03-05T10:00:00',
    metadataStatus: 'ok',
    needsResolution: false,
    effectiveCapturedAt: '2026-03-05T10:00:00',
    ...overrides
  }
}

describe('buildDestPath (moveToFolders)', () => {
  it('builds dest/YYYY/"YYYY MM"/filename for a normal record', () => {
    const record = baseRecord()
    const result = buildDestPath(record, 'D:\\Photos', '20260305@100000-01.jpg', 'moveToFolders')
    expect(result).toBe(['D:\\Photos', '2026', '2026 03', '20260305@100000-01.jpg'].join(sep))
  })

  it('routes to dest/Unsorted when destinationOverride is set', () => {
    const record = baseRecord({ destinationOverride: 'unsorted' })
    const result = buildDestPath(record, 'D:\\Photos', 'somefile-01.jpg', 'moveToFolders')
    expect(result).toBe(['D:\\Photos', 'Unsorted', 'somefile-01.jpg'].join(sep))
  })

  it('zero-pads the month in both the year folder and the "YYYY MM" folder', () => {
    const record = baseRecord({ effectiveCapturedAt: '2026-01-15T10:00:00' })
    const result = buildDestPath(record, 'D:\\Photos', 'file.jpg', 'moveToFolders')
    expect(result).toBe(['D:\\Photos', '2026', '2026 01', 'file.jpg'].join(sep))
  })
})

describe('buildDestPath (renameInPlace)', () => {
  it('keeps the file in its original folder, ignoring destDir and destinationOverride', () => {
    const record = baseRecord({
      originalPath: 'C:\\Photos\\vacation\\IMG_0001.jpg',
      destinationOverride: 'unsorted'
    })
    const result = buildDestPath(record, undefined, '20260305@100000-01.jpg', 'renameInPlace')
    expect(result).toBe(['C:\\Photos\\vacation', '20260305@100000-01.jpg'].join(sep))
  })
})
