import type { DateSource } from './DateSource'

export type MediaKind = 'photo' | 'video'

export interface GpsCoords {
  lat: number
  lon: number
}

/**
 * Canonical record for one file as it moves through the pipeline:
 * scan -> metadata extraction -> geocode -> (maybe) resolution -> sequence -> execute.
 * Lives in the main process (runState); the renderer only ever sees paginated slices.
 */
export interface ScanRecord {
  id: string
  originalPath: string
  mediaKind: MediaKind
  extension: string
  fileSize: number
  mtime: string

  exifCapturedAt?: string
  make?: string
  model?: string
  gps?: GpsCoords

  metadataStatus: 'ok' | 'unreadable'
  metadataError?: string

  location?: string
  geocodeStatus?: 'pending' | 'ok' | 'notFound' | 'error'

  needsResolution: boolean
  dateSource?: DateSource
  effectiveCapturedAt?: string
  manualCapturedAt?: string
  destinationOverride?: 'unsorted'

  sequenceNumber?: number
  finalFileName?: string
  finalDestPath?: string
}

export interface ResolutionDecision {
  id: string
  exclude?: boolean
  dateSource: Extract<DateSource, 'mtimeFallback' | 'manual'>
  manualCapturedAt?: string
  destinationOverride?: 'unsorted'
}

export interface MoveResult {
  id: string
  originalPath: string
  finalDestPath?: string
  status: 'moved' | 'copied' | 'failed' | 'skipped'
  error?: string
}
