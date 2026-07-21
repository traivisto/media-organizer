import Store from 'electron-store'
import type { AppConfig } from '../../shared/types/IpcContracts'
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
    windowsDatePattern: windowsFormat?.datePattern,
    windowsTimePattern: windowsFormat?.timePattern
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
