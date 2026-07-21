import type { ScanRecord } from '../../shared/types/ScanRecord'

interface RunState {
  sourceDir?: string
  destDir?: string
  records: ScanRecord[]
  skippedNonMedia: number
}

const state: RunState = {
  records: [],
  skippedNonMedia: 0
}

export function resetRunState(sourceDir: string, destDir: string | undefined): void {
  state.sourceDir = sourceDir
  state.destDir = destDir
  state.records = []
  state.skippedNonMedia = 0
}

export function getRunState(): RunState {
  return state
}

export function setRecords(records: ScanRecord[]): void {
  state.records = records
}

export function setSkippedNonMedia(count: number): void {
  state.skippedNonMedia = count
}

export function updateRecord(id: string, patch: Partial<ScanRecord>): void {
  const idx = state.records.findIndex((r) => r.id === id)
  if (idx !== -1) {
    state.records[idx] = { ...state.records[idx], ...patch }
  }
}
