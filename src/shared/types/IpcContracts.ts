import type { MoveResult, ResolutionDecision, ScanRecord } from './ScanRecord'

export const IpcChannels = {
  pickSourceDir: 'dialog:pickSourceDir',
  pickDestDir: 'dialog:pickDestDir',
  getConfig: 'config:get',

  scanStart: 'scan:start',
  scanCancel: 'scan:cancel',
  scanCancelled: 'scan:cancelled',
  scanProgress: 'scan:progress',
  scanNeedsResolution: 'scan:needsResolution',
  scanReadyForPreview: 'scan:readyForPreview',

  resolutionSubmit: 'resolution:submit',

  previewGet: 'preview:get',

  executeStart: 'execute:start',
  executeCancel: 'execute:cancel',
  executeProgress: 'execute:progress',
  executeDone: 'execute:done'
} as const

export interface AppConfig {
  lastSourceDir?: string
  lastDestDir?: string
  nominatimContact?: string
  /** date-fns-compatible short-date pattern read from this Windows PC's regional settings. */
  windowsDatePattern?: string
  /** date-fns-compatible short-time pattern read from this Windows PC's regional settings. */
  windowsTimePattern?: string
}

export type OrganizeMode = 'moveToFolders' | 'renameInPlace'

export interface ScanStartRequest {
  sourceDir: string
  destDir?: string
  includeSubfolders: boolean
  organizeMode: OrganizeMode
  includePhotos: boolean
  includeVideos: boolean
  /** Inclusive ISO date bounds (yyyy-MM-dd), filtered on exifCapturedAt falling back to mtime. */
  dateRangeStart?: string
  dateRangeEnd?: string
}

export interface ScanProgressEvent {
  phase: 'walking' | 'extracting' | 'geocoding'
  processed: number
  total: number
  currentPath?: string
  skippedNonMedia: number
}

export interface ScanNeedsResolutionEvent {
  records: ScanRecord[]
}

export interface ScanReadyForPreviewEvent {
  totalCount: number
  flaggedCount: number
  errorCount: number
  excludedByDateRange: number
  excludedManually: number
}

export interface ResolutionSubmitRequest {
  decisions: ResolutionDecision[]
}

export interface PreviewGetRequest {
  offset: number
  limit: number
}

export interface PreviewGetResponse {
  records: ScanRecord[]
  totalCount: number
}

export interface ExecuteProgressEvent {
  processed: number
  total: number
  currentPath?: string
}

export interface ExecuteDoneEvent {
  results: MoveResult[]
  movedCount: number
  failedCount: number
  skippedCount: number
  operationsLogPath: string
}
