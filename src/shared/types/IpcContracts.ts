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
  executeDone: 'execute:done',

  icloudSyncStart: 'icloud:syncStart',
  icloudSyncCancel: 'icloud:syncCancel',
  icloudSyncStatus: 'icloud:syncStatus'
} as const

export interface AppConfig {
  lastSourceDir?: string
  lastDestDir?: string
  nominatimContact?: string
  /** Apple ID email used for the last iCloud sync - never the password, which is
   *  entered directly into icloudpd's own sign-in page and never touches this app. */
  lastAppleId?: string
  /** date-fns-compatible short-date pattern read from this Windows PC's regional settings. */
  windowsDatePattern?: string
  /** date-fns-compatible short-time pattern read from this Windows PC's regional settings. */
  windowsTimePattern?: string
}

export interface IcloudSyncStartRequest {
  appleId: string
  includePhotos: boolean
  includeVideos: boolean
  /** Inclusive ISO date bounds (yyyy-MM-dd) - same fields as the scan's own date range filter. */
  dateRangeStart?: string
  dateRangeEnd?: string
}

export type IcloudSyncPhase =
  | 'starting'
  | 'awaitingAuth'
  | 'authenticated'
  | 'syncing'
  | 'done'
  | 'error'
  | 'cancelled'

export interface IcloudSyncStatusEvent {
  phase: IcloudSyncPhase
  message?: string
  downloadedCount?: number
  /** Populated once phase is 'done' - the local folder the synced files landed in, ready to be
   *  used as the scan's sourceDir. */
  syncDir?: string
  error?: string
  /** Full raw icloudpd output for this run, persisted to disk - useful for diagnosing a
   *  silent hang/crash that doesn't produce a clear error line. */
  logPath?: string
}

export type OrganizeMode = 'moveToFolders' | 'renameInPlace' | 'copyToFolders'

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
  copiedCount: number
  failedCount: number
  skippedCount: number
  operationsLogPath: string
}
