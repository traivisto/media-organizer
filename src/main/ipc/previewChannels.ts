import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { PreviewGetRequest, PreviewGetResponse } from '../../shared/types/IpcContracts'
import { getAllRecords } from '../orchestrator/scanOrchestrator'

export function registerPreviewChannels(): void {
  ipcMain.handle(IpcChannels.previewGet, (_event, req: PreviewGetRequest): PreviewGetResponse => {
    const all = getAllRecords()
    const records = all.slice(req.offset, req.offset + req.limit)
    return { records, totalCount: all.length }
  })
}
