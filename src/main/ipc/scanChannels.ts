import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { ScanStartRequest } from '../../shared/types/IpcContracts'
import { cancelScan, startScan } from '../orchestrator/scanOrchestrator'

export function registerScanChannels(): void {
  ipcMain.handle(IpcChannels.scanStart, async (_event, req: ScanStartRequest) => {
    await startScan(req)
  })

  ipcMain.handle(IpcChannels.scanCancel, () => {
    cancelScan()
  })
}
