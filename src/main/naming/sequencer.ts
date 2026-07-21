import type { ScanRecord } from '../../shared/types/ScanRecord'

function effectiveTimeMs(record: ScanRecord): number {
  const iso = record.effectiveCapturedAt ?? record.exifCapturedAt ?? record.mtime
  return new Date(iso).getTime()
}

/**
 * Sorts the ENTIRE batch by effective capture time ascending (tiebreak: original path,
 * for deterministic/reproducible runs) and assigns a global 1-based sequence number.
 * Requires every record to already have an effective date (post-resolution).
 */
export function assignSequenceNumbers(records: ScanRecord[]): ScanRecord[] {
  const sorted = [...records].sort((a, b) => {
    const diff = effectiveTimeMs(a) - effectiveTimeMs(b)
    if (diff !== 0) return diff
    return a.originalPath.localeCompare(b.originalPath)
  })

  return sorted.map((record, index) => ({ ...record, sequenceNumber: index + 1 }))
}
