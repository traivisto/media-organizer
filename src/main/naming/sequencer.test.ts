import { describe, expect, it } from 'vitest'
import { assignSequenceNumbers } from './sequencer'
import type { ScanRecord } from '../../shared/types/ScanRecord'

function makeRecord(id: string, effectiveCapturedAt: string, originalPath: string): ScanRecord {
  return {
    id,
    originalPath,
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 100,
    mtime: effectiveCapturedAt,
    metadataStatus: 'ok',
    needsResolution: false,
    effectiveCapturedAt
  }
}

describe('assignSequenceNumbers', () => {
  it('assigns global sequence numbers in chronological order', () => {
    const records = [
      makeRecord('c', '2026-03-01T10:00:00Z', 'C.jpg'),
      makeRecord('a', '2026-01-01T10:00:00Z', 'A.jpg'),
      makeRecord('b', '2026-02-01T10:00:00Z', 'B.jpg')
    ]

    const result = assignSequenceNumbers(records)

    expect(result.map((r) => r.id)).toEqual(['a', 'b', 'c'])
    expect(result.map((r) => r.sequenceNumber)).toEqual([1, 2, 3])
  })

  it('tiebreaks equal timestamps by original path for determinism', () => {
    const records = [
      makeRecord('z', '2026-01-01T10:00:00Z', 'z.jpg'),
      makeRecord('a', '2026-01-01T10:00:00Z', 'a.jpg')
    ]

    const result = assignSequenceNumbers(records)

    expect(result.map((r) => r.id)).toEqual(['a', 'z'])
  })
})
