import { ipcMain, shell } from 'electron'
import { stat } from 'fs/promises'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { ArchiveUpdateStatusEvent } from '../../shared/types/IpcContracts'
import { cancelArchiveUpdate, startArchiveUpdate } from '../archive/archiveUpdate'
import { getMediaidxPath } from '../config/appConfig'
import { sendToRenderer } from './rendererBridge'

export function registerArchiveChannels(): void {
  ipcMain.handle(IpcChannels.archiveUpdateStart, async () => {
    await startArchiveUpdate(getMediaidxPath(), (status: ArchiveUpdateStatusEvent) => {
      sendToRenderer(IpcChannels.archiveUpdateStatus, status)
    })
  })

  ipcMain.handle(IpcChannels.archiveUpdateCancel, () => {
    cancelArchiveUpdate()
  })

  // Opens a folder in Explorer so the new photos can be looked through (and pruned) before
  // the archive is updated. Folders only: never hands a file to its default program.
  ipcMain.handle(IpcChannels.openFolder, async (_event, dir: string) => {
    const s = await stat(dir).catch(() => undefined)
    if (!s?.isDirectory()) return `Not a folder: ${dir}`
    const error = await shell.openPath(dir)
    return error || undefined
  })
}
