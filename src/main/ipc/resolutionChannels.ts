import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { ResolutionSubmitRequest } from '../../shared/types/IpcContracts'
import { submitResolution } from '../orchestrator/resolutionGate'

export function registerResolutionChannels(): void {
  ipcMain.handle(IpcChannels.resolutionSubmit, (_event, req: ResolutionSubmitRequest) => {
    submitResolution(req.decisions)
  })
}
