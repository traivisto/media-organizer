import { dialog, ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import {
  getConfig,
  getLastDestDir,
  getLastSourceDir,
  setLastDestDir,
  setLastPreviewColumnWidths,
  setLastSourceDir,
  setLastSourceType
} from '../config/appConfig'

export function registerDialogChannels(): void {
  ipcMain.handle(IpcChannels.pickSourceDir, async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
      properties: ['openDirectory'],
      defaultPath: getLastSourceDir()
    })
    if (canceled || filePaths.length === 0) return undefined
    setLastSourceDir(filePaths[0])
    setLastSourceType('local')
    return filePaths[0]
  })

  ipcMain.handle(IpcChannels.pickDestDir, async () => {
    const { canceled, filePaths } = await dialog.showOpenDialog({
      properties: ['openDirectory', 'createDirectory'],
      defaultPath: getLastDestDir()
    })
    if (canceled || filePaths.length === 0) return undefined
    setLastDestDir(filePaths[0])
    return filePaths[0]
  })

  ipcMain.handle(IpcChannels.getConfig, () => getConfig())

  ipcMain.handle(IpcChannels.setPreviewColumnWidths, (_event, widths: number[]) => {
    setLastPreviewColumnWidths(widths)
  })
}
