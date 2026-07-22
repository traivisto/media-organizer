import { create } from 'zustand'
import type {
  ExecuteDoneEvent,
  ExecuteProgressEvent,
  IcloudSyncStatusEvent,
  OrganizeMode,
  ScanProgressEvent,
  ScanReadyForPreviewEvent,
  SourceType
} from '../../../shared/types/IpcContracts'
import type { ScanRecord } from '../../../shared/types/ScanRecord'

export type Phase =
  | 'pickDirs'
  | 'icloudSync'
  | 'scanning'
  | 'resolution'
  | 'preview'
  | 'executing'
  | 'results'

interface AppState {
  phase: Phase
  sourceDir?: string
  destDir?: string
  organizeMode: OrganizeMode
  windowsDatePattern?: string
  windowsTimePattern?: string
  scanProgress?: ScanProgressEvent
  needsResolutionRecords: ScanRecord[]
  totalCount: number
  flaggedCount: number
  errorCount: number
  excludedByDateRange: number
  excludedManually: number
  executeProgress?: ExecuteProgressEvent
  executeResult?: ExecuteDoneEvent
  icloudSyncStatus?: IcloudSyncStatusEvent

  // Lifted out of PickDirsView's local state so they survive the icloudSync phase
  // transition (PickDirsView unmounts while ICloudSyncView is showing) and are still
  // there for ICloudSyncView to reuse when it starts the scan itself after sync completes.
  sourceType: SourceType
  includeSubfolders: boolean
  includePhotos: boolean
  includeVideos: boolean
  dateRangeStart: string
  dateRangeEnd: string
  appleId: string

  setSourceDir: (dir?: string) => void
  setDestDir: (dir?: string) => void
  setOrganizeMode: (mode: OrganizeMode) => void
  setWindowsDateTimeFormat: (datePattern?: string, timePattern?: string) => void
  setPhase: (phase: Phase) => void
  setScanProgress: (progress: ScanProgressEvent) => void
  setNeedsResolutionRecords: (records: ScanRecord[]) => void
  setPreviewSummary: (summary: ScanReadyForPreviewEvent) => void
  setExecuteProgress: (progress: ExecuteProgressEvent) => void
  setExecuteResult: (result: ExecuteDoneEvent) => void
  setIcloudSyncStatus: (status?: IcloudSyncStatusEvent) => void
  setSourceType: (type: SourceType) => void
  setIncludeSubfolders: (value: boolean) => void
  setIncludePhotos: (value: boolean) => void
  setIncludeVideos: (value: boolean) => void
  setDateRangeStart: (value: string) => void
  setDateRangeEnd: (value: string) => void
  setAppleId: (value: string) => void
}

export const useAppStore = create<AppState>((set) => ({
  phase: 'pickDirs',
  organizeMode: 'moveToFolders',
  needsResolutionRecords: [],
  totalCount: 0,
  flaggedCount: 0,
  errorCount: 0,
  excludedByDateRange: 0,
  excludedManually: 0,

  sourceType: 'local',
  includeSubfolders: true,
  includePhotos: true,
  includeVideos: true,
  dateRangeStart: '',
  dateRangeEnd: '',
  appleId: '',

  setSourceDir: (dir) => set({ sourceDir: dir }),
  setDestDir: (dir) => set({ destDir: dir }),
  setOrganizeMode: (organizeMode) => set({ organizeMode }),
  setWindowsDateTimeFormat: (windowsDatePattern, windowsTimePattern) =>
    set({ windowsDatePattern, windowsTimePattern }),
  setPhase: (phase) => set({ phase }),
  setScanProgress: (progress) => set({ scanProgress: progress }),
  setNeedsResolutionRecords: (records) => set({ needsResolutionRecords: records }),
  setPreviewSummary: (summary) =>
    set({
      totalCount: summary.totalCount,
      flaggedCount: summary.flaggedCount,
      errorCount: summary.errorCount,
      excludedByDateRange: summary.excludedByDateRange,
      excludedManually: summary.excludedManually
    }),
  setExecuteProgress: (progress) => set({ executeProgress: progress }),
  setExecuteResult: (result) => set({ executeResult: result }),
  setIcloudSyncStatus: (status) => set({ icloudSyncStatus: status }),
  setSourceType: (sourceType) => set({ sourceType }),
  setIncludeSubfolders: (includeSubfolders) => set({ includeSubfolders }),
  setIncludePhotos: (includePhotos) => set({ includePhotos }),
  setIncludeVideos: (includeVideos) => set({ includeVideos }),
  setDateRangeStart: (dateRangeStart) => set({ dateRangeStart }),
  setDateRangeEnd: (dateRangeEnd) => set({ dateRangeEnd }),
  setAppleId: (appleId) => set({ appleId })
}))
