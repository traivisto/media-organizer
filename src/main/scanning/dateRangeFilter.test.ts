import { describe, expect, it } from 'vitest'
import { isWithinDateRange, isWalkedFileWithinDateRange } from './dateRangeFilter'
import type { ScanRecord } from '../../shared/types/ScanRecord'
import type { WalkedFile } from './fileWalker'

function baseRecord(overrides: Partial<ScanRecord> = {}): ScanRecord {
  return {
    id: '1',
    originalPath: 'a.jpg',
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 10,
    mtime: '2026-06-15T10:00:00',
    metadataStatus: 'ok',
    needsResolution: false,
    ...overrides
  }
}

describe('isWithinDateRange', () => {
  it('includes everything when no bounds are given', () => {
    expect(isWithinDateRange(baseRecord())).toBe(true)
  })

  it('excludes a record before the start bound', () => {
    const record = baseRecord({ exifCapturedAt: '2026-01-01T10:00:00' })
    expect(isWithinDateRange(record, '2026-02-01')).toBe(false)
  })

  it('excludes a record after the end bound', () => {
    const record = baseRecord({ exifCapturedAt: '2026-12-31T10:00:00' })
    expect(isWithinDateRange(record, undefined, '2026-06-30')).toBe(false)
  })

  it('includes a record within both bounds', () => {
    const record = baseRecord({ exifCapturedAt: '2026-03-15T10:00:00' })
    expect(isWithinDateRange(record, '2026-01-01', '2026-06-30')).toBe(true)
  })

  it('includes the boundary days themselves', () => {
    const record = baseRecord({ exifCapturedAt: '2026-02-01T00:00:00' })
    expect(isWithinDateRange(record, '2026-02-01', '2026-02-01')).toBe(true)
  })

  it('falls back to mtime when exifCapturedAt is absent', () => {
    const record = baseRecord({ mtime: '2026-01-01T10:00:00' })
    expect(isWithinDateRange(record, '2026-02-01')).toBe(false)
  })
})

function baseWalkedFile(overrides: Partial<WalkedFile> = {}): WalkedFile {
  return {
    path: 'a.jpg',
    extension: '.jpg',
    mediaKind: 'photo',
    size: 10,
    mtime: new Date('2026-06-15T10:00:00'),
    ...overrides
  }
}

describe('isWalkedFileWithinDateRange', () => {
  it('includes everything when no bounds are given', () => {
    expect(isWalkedFileWithinDateRange(baseWalkedFile())).toBe(true)
  })

  it('excludes a file whose mtime falls before the start bound', () => {
    const file = baseWalkedFile({ mtime: new Date('2026-01-01T10:00:00') })
    expect(isWalkedFileWithinDateRange(file, '2026-02-01')).toBe(false)
  })

  it('excludes a file whose mtime falls after the end bound', () => {
    const file = baseWalkedFile({ mtime: new Date('2026-12-31T10:00:00') })
    expect(isWalkedFileWithinDateRange(file, undefined, '2026-06-30')).toBe(false)
  })

  it('includes a file whose mtime falls within both bounds', () => {
    const file = baseWalkedFile({ mtime: new Date('2026-03-15T10:00:00') })
    expect(isWalkedFileWithinDateRange(file, '2026-01-01', '2026-06-30')).toBe(true)
  })
})
