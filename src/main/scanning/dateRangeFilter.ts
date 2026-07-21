import type { ScanRecord } from '../../shared/types/ScanRecord'
import type { WalkedFile } from './fileWalker'

/**
 * Inclusive bounds check shared by the mtime pre-filter and the EXIF-aware post-filter
 * below. start/end are "yyyy-MM-dd" calendar dates in the user's local time, as produced
 * by an HTML date input.
 */
function isDateWithinRange(date: Date, start?: string, end?: string): boolean {
  if (!start && !end) return true
  if (start && date < new Date(`${start}T00:00:00`)) return false
  if (end && date > new Date(`${end}T23:59:59.999`)) return false
  return true
}

/**
 * Inclusive filter on a record's best-known capture date (exifCapturedAt, falling
 * back to filesystem mtime - the same fallback order used everywhere else in the
 * pipeline).
 */
export function isWithinDateRange(record: ScanRecord, start?: string, end?: string): boolean {
  return isDateWithinRange(new Date(record.exifCapturedAt ?? record.mtime), start, end)
}

/**
 * Fast pre-filter on a freshly-walked file's filesystem mtime, run before the expensive
 * per-file exiftool read - so a narrow date range can skip metadata extraction entirely
 * for files nowhere near it, instead of extracting everything first and filtering after
 * (the previous behavior, which made the date range filter useless for speeding up scans
 * of large folders). mtime is usually preserved on copy, but isn't as reliable as EXIF
 * capture date, so this only narrows the set handed to exiftool - the precise EXIF-aware
 * isWithinDateRange check above still runs afterward on whatever survives.
 */
export function isWalkedFileWithinDateRange(file: WalkedFile, start?: string, end?: string): boolean {
  return isDateWithinRange(file.mtime, start, end)
}
