import type { BrowserWindow } from 'electron'

let mainWindow: BrowserWindow | undefined

export function setMainWindow(win: BrowserWindow): void {
  mainWindow = win
}

export function sendToRenderer<T>(channel: string, payload: T): void {
  mainWindow?.webContents.send(channel, payload)
}
