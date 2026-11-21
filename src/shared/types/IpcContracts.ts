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
  scanNeedsTimeCorrection: 'scan:needsTimeCorrection',
  scanReadyForPreview: 'scan:readyForPreview',

  resolutionSubmit: 'resolution:submit',
  timeCorrectionSubmit: 'timeCorrection:submit',

  previewGet: 'preview:get',

  executeStart: 'execute:start',
  executeCancel: 'execute:cancel',
  executeProgress: 'execute:progress',
  executeDone: 'execute:done',

  icloudSyncStart: 'icloud:syncStart',
  icloudSyncCancel: 'icloud:syncCancel',
  icloudSyncStatus: 'icloud:syncStatus',

  setPreviewColumnWidths: 'config:setPreviewColumnWidths',

  archiveUpdateStart: 'archive:updateStart',
  archiveUpdateCancel: 'archive:updateCancel',
  archiveUpdateStatus: 'archive:updateStatus',
  openFolder: 'shell:openFolder'
} as const

export type SourceType = 'local' | 'icloud'

export interface WindowBounds {
  width: number
  height: number
  x?: number
  y?: number
}

export interface AppConfig {
  lastSourceDir?: string
  lastDestDir?: string
  nominatimContact?: string
  /** Apple ID email used for the last iCloud sync - never the password, which is
   *  entered directly into icloudpd's own sign-in page and never touches this app. */
  lastAppleId?: string
  /** Which source option was last used, so PickDirsView can restore the same radio choice. */
  lastSourceType?: SourceType
  /** date-fns-compatible short-date pattern read from this Windows PC's regional settings. */
  windowsDatePattern?: string
  /** date-fns-compatible short-time pattern read from this Windows PC's regional settings. */
  windowsTimePattern?: string
  lastWindowBounds?: WindowBounds
  lastPreviewColumnWidths?: number[]
  /** Where the photo archive's `mediaidx.exe` is; unset means the default under ~\.mediaidx. */
  mediaidxPath?: string
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

export type OrganizeMode = 'moveToFolders' | 'renameInPlace' | 'copyToFolders' | 'fixTimeOrder'

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

/** One detected camera (by make/model) found in the scanned batch, with its file count
 *  and capture-date range - shown on the time-correction screen so the user can decide
 *  an offset per camera. Records with no make/model are grouped under make/model both
 *  undefined ("Unknown camera"). */
export interface CameraGroup {
  make?: string
  model?: string
  count: number
  minCapturedAt: string
  maxCapturedAt: string
}

export interface ScanNeedsTimeCorrectionEvent {
  groups: CameraGroup[]
}

export interface TimeCorrectionRule {
  make?: string
  model?: string
  /** Signed offset in minutes added to exifCapturedAt, e.g. -120 for "2 hours earlier". */
  offsetMinutes: number
}

export interface TimeCorrectionSubmitRequest {
  rules: TimeCorrectionRule[]
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

/** The stages of `mediaidx update`, in the order it runs them. `publish` is left out when
 *  the photo archive has nowhere configured to publish to. */
export type ArchiveUpdateStage = 'scan' | 'embed' | 'faces' | 'cluster' | 'derive' | 'publish'

export interface ArchiveUpdateSummary {
  newPhotographs: number
  /** Files the archive had indexed that are no longer there (e.g. deleted while reviewing). */
  missingFiles: number
  newFaces: number
  newlyNamedFaces: number
  published: boolean
  /** New year folders (e.g. ...\Pictures\2027) the archive now watches. */
  addedRoots: string[]
}

export interface ArchiveUpdateStatusEvent {
  state: 'running' | 'done' | 'error' | 'cancelled'
  stage?: ArchiveUpdateStage
  stageIndex?: number
  stageTotal?: number
  /** The latest line of mediaidx's own output, for a sense of progress within a stage. */
  detail?: string
  addedRoots?: string[]
  summary?: ArchiveUpdateSummary
  error?: string
  logPath?: string
}

export interface ExecuteDoneEvent {
  results: MoveResult[]
  movedCount: number
  copiedCount: number
  failedCount: number
  skippedCount: number
  operationsLogPath: string
}
