import { describe, expect, it } from 'vitest'
import { buildCameraGroups, applyTimeCorrection } from './timeCorrection'
import type { ScanRecord } from '../../shared/types/ScanRecord'

function baseRecord(overrides: Partial<ScanRecord> = {}): ScanRecord {
  return {
    id: overrides.id ?? '1',
    originalPath: '',
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 10,
    mtime: '2026-01-01T00:00:00.000Z',
    metadataStatus: 'ok',
    needsResolution: false,
    ...overrides
  }
}

describe('buildCameraGroups', () => {
  it('groups records by make+model and tracks count and date range', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-01T08:00:00.000Z',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z'
      }),
      baseRecord({
        id: 'b',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-03T10:00:00.000Z',
        effectiveCapturedAt: '2026-05-03T10:00:00.000Z'
      }),
      baseRecord({
        id: 'c',
        make: 'Sony',
        model: 'A7 III',
        exifCapturedAt: '2026-05-02T09:00:00.000Z',
        effectiveCapturedAt: '2026-05-02T09:00:00.000Z'
      })
    ]

    const groups = buildCameraGroups(records)

    expect(groups).toHaveLength(2)
    const canon = groups.find((g) => g.model === 'EOS 90D')!
    expect(canon.count).toBe(2)
    expect(canon.minCapturedAt).toBe('2026-05-01T08:00:00.000Z')
    expect(canon.maxCapturedAt).toBe('2026-05-03T10:00:00.000Z')
  })

  it('ignores records without any base date yet', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-01T08:00:00.000Z',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z'
      }),
      baseRecord({ id: 'b', make: 'Canon', model: 'EOS 90D' })
    ]

    const groups = buildCameraGroups(records)

    expect(groups).toHaveLength(1)
    expect(groups[0].count).toBe(1)
  })

  it('groups records with no make/model together as "no camera info"', () => {
    const records = [
      baseRecord({ id: 'a', effectiveCapturedAt: '2026-05-01T08:00:00.000Z' }),
      baseRecord({ id: 'b', effectiveCapturedAt: '2026-05-02T08:00:00.000Z' })
    ]

    const groups = buildCameraGroups(records)

    expect(groups).toHaveLength(1)
    expect(groups[0].make).toBeUndefined()
    expect(groups[0].model).toBeUndefined()
    expect(groups[0].count).toBe(2)
  })

  it('groups files with no EXIF at all (e.g. saved from WhatsApp) using their mtime as the base date', () => {
    const records = [
      baseRecord({ id: 'a', mtime: '2026-05-01T08:00:00.000Z', effectiveCapturedAt: '2026-05-01T08:00:00.000Z', dateSource: 'mtimeFallback' })
    ]

    const groups = buildCameraGroups(records)

    expect(groups).toHaveLength(1)
    expect(groups[0].make).toBeUndefined()
    expect(groups[0].minCapturedAt).toBe('2026-05-01T08:00:00.000Z')
  })
})

describe('applyTimeCorrection', () => {
  it('shifts effectiveCapturedAt by the matching camera offset and tags the dateSource', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-01T08:00:00.000Z',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z',
        dateSource: 'exif'
      })
    ]

    applyTimeCorrection(records, [{ make: 'Canon', model: 'EOS 90D', offsetMinutes: -120 }])

    expect(records[0].effectiveCapturedAt).toBe('2026-05-01T06:00:00.000Z')
    expect(records[0].dateSource).toBe('timezoneCorrected')
  })

  it('shifts the "no camera info" group (make/model both undefined) too', () => {
    const records = [
      baseRecord({
        id: 'a',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z',
        dateSource: 'mtimeFallback'
      })
    ]

    applyTimeCorrection(records, [{ offsetMinutes: -60 }])

    expect(records[0].effectiveCapturedAt).toBe('2026-05-01T07:00:00.000Z')
    expect(records[0].dateSource).toBe('timezoneCorrected')
  })

  it('leaves records untouched when no rule matches their camera', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Sony',
        model: 'A7 III',
        exifCapturedAt: '2026-05-01T08:00:00.000Z',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z',
        dateSource: 'exif'
      })
    ]

    applyTimeCorrection(records, [{ make: 'Canon', model: 'EOS 90D', offsetMinutes: -120 }])

    expect(records[0].effectiveCapturedAt).toBe('2026-05-01T08:00:00.000Z')
    expect(records[0].dateSource).toBe('exif')
  })

  it('treats a zero offset as no change', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-01T08:00:00.000Z',
        effectiveCapturedAt: '2026-05-01T08:00:00.000Z',
        dateSource: 'exif'
      })
    ]

    applyTimeCorrection(records, [{ make: 'Canon', model: 'EOS 90D', offsetMinutes: 0 }])

    expect(records[0].dateSource).toBe('exif')
  })

  it('applies a positive offset across a day boundary', () => {
    const records = [
      baseRecord({
        id: 'a',
        make: 'Canon',
        model: 'EOS 90D',
        exifCapturedAt: '2026-05-01T23:30:00.000Z',
        effectiveCapturedAt: '2026-05-01T23:30:00.000Z',
        dateSource: 'exif'
      })
    ]

    applyTimeCorrection(records, [{ make: 'Canon', model: 'EOS 90D', offsetMinutes: 90 }])

    expect(records[0].effectiveCapturedAt).toBe('2026-05-02T01:00:00.000Z')
  })
})
