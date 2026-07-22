import Store from 'electron-store'
import type { AppConfig, SourceType, WindowBounds } from '../../shared/types/IpcContracts'
import { getWindowsDateTimeFormat } from './windowsRegionalFormat'

// Nominatim's usage policy requires requests to identify the application (and ideally
// a contact) via User-Agent. Stored locally (electron-store JSON in userData), not in
// source control - edit that file directly if you want to add your own contact info.
const DEFAULT_NOMINATIM_CONTACT = 'media-organizer/0.9 (personal-use desktop app)'

const store = new Store<AppConfig>({
  name: 'media-organizer-config',
  defaults: {
    nominatimContact: DEFAULT_NOMINATIM_CONTACT
  }
})

export async function getConfig(): Promise<AppConfig> {
  const windowsFormat = await getWindowsDateTimeFormat()
  return {
    lastSourceDir: store.get('lastSourceDir'),
    lastDestDir: store.get('lastDestDir'),
    nominatimContact: store.get('nominatimContact'),
    lastAppleId: store.get('lastAppleId'),
    lastSourceType: store.get('lastSourceType'),
    windowsDatePattern: windowsFormat?.datePattern,
    windowsTimePattern: windowsFormat?.timePattern,
    lastWindowBounds: store.get('lastWindowBounds'),
    lastPreviewColumnWidths: store.get('lastPreviewColumnWidths')
  }
}

export function getLastSourceDir(): string | undefined {
  return store.get('lastSourceDir')
}

export function getLastDestDir(): string | undefined {
  return store.get('lastDestDir')
}

export function setLastSourceDir(dir: string): void {
  store.set('lastSourceDir', dir)
}

export function setLastDestDir(dir: string): void {
  store.set('lastDestDir', dir)
}

export function getNominatimContact(): string | undefined {
  return store.get('nominatimContact')
}

export function setLastAppleId(appleId: string): void {
  store.set('lastAppleId', appleId)
}

export function setLastSourceType(type: SourceType): void {
  store.set('lastSourceType', type)
}

export function getLastWindowBounds(): WindowBounds | undefined {
  return store.get('lastWindowBounds')
}

export function setLastWindowBounds(bounds: WindowBounds): void {
  store.set('lastWindowBounds', bounds)
}

export function setLastPreviewColumnWidths(widths: number[]): void {
  store.set('lastPreviewColumnWidths', widths)
}
