import type { Tags } from 'exiftool-vendored'
import type { WalkedFile } from '../scanning/fileWalker'
import type { ExiftoolReadResult } from './exiftoolClient'
import type { ScanRecord } from '../../shared/types/ScanRecord'

const MIN_PLAUSIBLE_YEAR = 1990

function tagToDate(value: unknown): Date | undefined {
  if (!value) return undefined
  if (value instanceof Date) return value
  if (typeof value === 'object' && value !== null && 'toDate' in value) {
    try {
      const d = (value as { toDate: () => Date }).toDate()
      return d
    } catch {
      return undefined
    }
  }
  if (typeof value === 'string') {
    const d = new Date(value)
    if (!Number.isNaN(d.getTime())) return d
  }
  return undefined
}

function isPlausibleCaptureDate(d: Date): boolean {
  const year = d.getFullYear()
  if (year < MIN_PLAUSIBLE_YEAR) return false
  if (d.getTime() > Date.now() + 24 * 60 * 60 * 1000) return false // more than 1 day in the future
  return true
}

function extractCapturedAt(tags: Tags): Date | undefined {
  const candidates = [tags.DateTimeOriginal, tags.CreateDate, tags.MediaCreateDate]
  for (const candidate of candidates) {
    const d = tagToDate(candidate)
    if (d && isPlausibleCaptureDate(d)) return d
  }
  return undefined
}

function extractGps(tags: Tags): { lat: number; lon: number } | undefined {
  const lat = tags.GPSLatitude
  const lon = tags.GPSLongitude
  if (typeof lat === 'number' && typeof lon === 'number' && (lat !== 0 || lon !== 0)) {
    return { lat, lon }
  }
  return undefined
}

export function normalizeMetadata(
  file: WalkedFile,
  result: ExiftoolReadResult,
  id: string
): ScanRecord {
  const base: ScanRecord = {
    id,
    originalPath: file.path,
    mediaKind: file.mediaKind,
    extension: file.extension,
    fileSize: file.size,
    mtime: file.mtime.toISOString(),
    metadataStatus: 'ok',
    needsResolution: false
  }

  if (result.error || !result.tags) {
    return {
      ...base,
      metadataStatus: 'unreadable',
      metadataError: result.error ?? 'No metadata returned',
      needsResolution: true
    }
  }

  const tags = result.tags
  const capturedAt = extractCapturedAt(tags)
  const gps = extractGps(tags)

  return {
    ...base,
    exifCapturedAt: capturedAt?.toISOString(),
    make: tags.Make?.trim() || undefined,
    model: tags.Model?.trim() || undefined,
    gps,
    needsResolution: !capturedAt
  }
}
