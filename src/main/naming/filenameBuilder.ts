import { format } from 'date-fns'
import type { ScanRecord } from '../../shared/types/ScanRecord'
import { sanitizeForWindows } from './sanitize'

/**
 * Builds "YYYYMMDD@HHMMSS-[Camera]-[Location]-[kuvaus]-[N].ext", omitting the Camera
 * and/or Location segments (and their leading dash) when not available. Video files
 * get an extra "kuvaus" placeholder segment - a manual-edit reminder the user fills
 * in later with a short description of the video's content.
 */
export function buildFileName(record: ScanRecord, totalCount: number): string {
  const iso = record.effectiveCapturedAt ?? record.exifCapturedAt ?? record.mtime
  const date = new Date(iso)
  const timestamp = format(date, "yyyyMMdd'@'HHmmss")

  const segments = [timestamp]

  if (record.model) {
    const camera = sanitizeForWindows(record.model)
    if (camera) segments.push(camera)
  }

  if (record.location) {
    const location = sanitizeForWindows(record.location)
    if (location) segments.push(location)
  }

  if (record.mediaKind === 'video') {
    segments.push('kuvaus')
  }

  const width = String(totalCount).length
  const n = String(record.sequenceNumber ?? 0).padStart(width, '0')
  segments.push(n)

  const ext = record.extension.toLowerCase()
  return `${segments.join('-')}${ext}`
}
