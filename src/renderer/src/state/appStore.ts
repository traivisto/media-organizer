import { create } from 'zustand'
import type {
  ExecuteDoneEvent,
  ExecuteProgressEvent,
  OrganizeMode,
  ScanProgressEvent,
  ScanReadyForPreviewEvent
} from '../../../shared/types/IpcContracts'
import type { ScanRecord } from '../../../shared/types/ScanRecord'

export type Phase = 'pickDirs' | 'scanning' | 'resolution' | 'preview' | 'executing' | 'results'

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
  setExecuteResult: (result) => set({ executeResult: result })
}))
