import { describe, expect, it } from 'vitest'
import { buildFileName } from './filenameBuilder'
import type { ScanRecord } from '../../shared/types/ScanRecord'

function baseRecord(overrides: Partial<ScanRecord> = {}): ScanRecord {
  return {
    id: '1',
    originalPath: 'IMG_0001.jpg',
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 100,
    mtime: '2026-07-21T14:32:05',
    metadataStatus: 'ok',
    needsResolution: false,
    effectiveCapturedAt: '2026-07-21T14:32:05',
    sequenceNumber: 3,
    ...overrides
  }
}

describe('buildFileName', () => {
  it('includes camera and location when both present', () => {
    const record = baseRecord({ model: 'iPhone 14 Pro', location: 'Helsinki' })
    expect(buildFileName(record, 12)).toBe('20260721@143205-iPhone 14 Pro-Helsinki-03.jpg')
  })

  it('omits the camera segment when model is absent', () => {
    const record = baseRecord({ location: 'Helsinki' })
    expect(buildFileName(record, 12)).toBe('20260721@143205-Helsinki-03.jpg')
  })

  it('omits the location segment when not geocoded', () => {
    const record = baseRecord({ model: 'iPhone 14 Pro' })
    expect(buildFileName(record, 12)).toBe('20260721@143205-iPhone 14 Pro-03.jpg')
  })

  it('omits both when neither is present', () => {
    const record = baseRecord()
    expect(buildFileName(record, 12)).toBe('20260721@143205-03.jpg')
  })

  it('pads the sequence number to the width of the total batch size', () => {
    const record = baseRecord({ sequenceNumber: 7 })
    expect(buildFileName(record, 1234)).toBe('20260721@143205-0007.jpg')
  })

  it('adds a "kuvaus" placeholder segment for video files, after camera/location and before N', () => {
    const record = baseRecord({
      mediaKind: 'video',
      extension: '.mp4',
      model: 'iPhone 14 Pro',
      location: 'Helsinki'
    })
    expect(buildFileName(record, 12)).toBe('20260721@143205-iPhone 14 Pro-Helsinki-kuvaus-03.mp4')
  })

  it('adds "kuvaus" for videos even when camera and location are both absent', () => {
    const record = baseRecord({ mediaKind: 'video', extension: '.mp4' })
    expect(buildFileName(record, 12)).toBe('20260721@143205-kuvaus-03.mp4')
  })

  it('does not add "kuvaus" for photos', () => {
    const record = baseRecord({ model: 'iPhone 14 Pro' })
    expect(buildFileName(record, 12)).not.toContain('kuvaus')
  })
})
