import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { IcloudSyncStartRequest, IcloudSyncStatusEvent } from '../../shared/types/IpcContracts'
import { cancelSync, runSync } from '../icloud/icloudSyncEngine'
import { setLastAppleId } from '../config/appConfig'
import { sendToRenderer } from './rendererBridge'

export function registerIcloudChannels(): void {
  ipcMain.handle(IpcChannels.icloudSyncStart, async (_event, req: IcloudSyncStartRequest) => {
    setLastAppleId(req.appleId)
    await runSync(req, (status: IcloudSyncStatusEvent) => {
      sendToRenderer(IpcChannels.icloudSyncStatus, status)
    })
  })

  ipcMain.handle(IpcChannels.icloudSyncCancel, () => {
    cancelSync()
  })
}
