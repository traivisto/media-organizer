import type { ScanRecord } from '../../shared/types/ScanRecord'

/**
 * Inclusive filter on a record's best-known capture date (exifCapturedAt, falling
 * back to filesystem mtime - the same fallback order used everywhere else in the
 * pipeline). start/end are "yyyy-MM-dd" calendar dates in the user's local time,
 * as produced by an HTML date input.
 */
export function isWithinDateRange(record: ScanRecord, start?: string, end?: string): boolean {
  if (!start && !end) return true

  const date = new Date(record.exifCapturedAt ?? record.mtime)
  if (start && date < new Date(`${start}T00:00:00`)) return false
  if (end && date > new Date(`${end}T23:59:59.999`)) return false
  return true
}
