import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { TimeCorrectionSubmitRequest } from '../../shared/types/IpcContracts'
import { submitTimeCorrection } from '../orchestrator/timeCorrectionGate'

export function registerTimeCorrectionChannels(): void {
  ipcMain.handle(IpcChannels.timeCorrectionSubmit, (_event, req: TimeCorrectionSubmitRequest) => {
    submitTimeCorrection(req.rules)
  })
}
