import { app, BrowserWindow, shell } from 'electron'
import { join } from 'path'
import { electronApp, is, optimizer } from '@electron-toolkit/utils'
import { registerIpcHandlers } from './ipc'
import { setMainWindow } from './ipc/rendererBridge'
import { initExiftoolPool, shutdownExiftoolPool } from './metadata/exiftoolClient'
import { getWindowsLocaleNameSync } from './config/windowsRegionalFormat'
import { getLastWindowBounds, setLastWindowBounds } from './config/appConfig'

// Must run synchronously, before anything else yields to the event loop - Chromium's
// locale.pak selection (which drives navigator.language / Intl / native date-input
// rendering) is resolved too early for an appendSwitch call made after even one await,
// even though that's still technically "before app.whenReady()".
const windowsLocale = getWindowsLocaleNameSync()
if (windowsLocale) app.commandLine.appendSwitch('lang', windowsLocale)

function createWindow(): void {
  const bounds = getLastWindowBounds()
  const mainWindow = new BrowserWindow({
    width: bounds?.width ?? 1200,
    height: bounds?.height ?? 800,
    x: bounds?.x,
    y: bounds?.y,
    show: false,
    autoHideMenuBar: true,
    webPreferences: {
      preload: join(__dirname, '../preload/index.mjs'),
      sandbox: false
    }
  })

  setMainWindow(mainWindow)

  mainWindow.on('ready-to-show', () => {
    mainWindow.show()
  })

  mainWindow.on('close', () => {
    setLastWindowBounds(mainWindow.getBounds())
  })

  mainWindow.webContents.setWindowOpenHandler((details) => {
    shell.openExternal(details.url)
    return { action: 'deny' }
  })

  if (is.dev && process.env['ELECTRON_RENDERER_URL']) {
    mainWindow.loadURL(process.env['ELECTRON_RENDERER_URL'])
  } else {
    mainWindow.loadFile(join(__dirname, '../renderer/index.html'))
  }
}

async function main(): Promise<void> {
  await app.whenReady()

  electronApp.setAppUserModelId('com.traivisto.media-organizer')

  app.on('browser-window-created', (_, window) => {
    optimizer.watchWindowShortcuts(window)
  })

  initExiftoolPool()
  registerIpcHandlers()
  createWindow()

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
}

void main()

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') {
    app.quit()
  }
})

app.on('before-quit', () => {
  void shutdownExiftoolPool()
})
