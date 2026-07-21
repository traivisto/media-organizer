import { app } from 'electron'
import { promises as fs } from 'fs'
import { join } from 'path'

const CACHE_FILENAME = 'geocode-cache.json'
// ~3 decimal places is ~111m at the equator - plenty for "which city/place" resolution.
const COORD_PRECISION = 3

export interface CacheEntry {
  location: string | null // null = looked up, nothing found (negative cache)
}

let cache: Map<string, CacheEntry> | undefined
let loadPromise: Promise<void> | undefined

function cachePath(): string {
  return join(app.getPath('userData'), CACHE_FILENAME)
}

export function roundCoordKey(lat: number, lon: number): string {
  return `${lat.toFixed(COORD_PRECISION)},${lon.toFixed(COORD_PRECISION)}`
}

async function ensureLoaded(): Promise<void> {
  if (cache) return
  if (!loadPromise) {
    loadPromise = (async () => {
      try {
        const raw = await fs.readFile(cachePath(), 'utf-8')
        cache = new Map(Object.entries(JSON.parse(raw)))
      } catch {
        cache = new Map()
      }
    })()
  }
  await loadPromise
}

async function persist(): Promise<void> {
  if (!cache) return
  const obj = Object.fromEntries(cache)
  await fs.writeFile(cachePath(), JSON.stringify(obj, null, 2), 'utf-8')
}

export async function getCachedLocation(lat: number, lon: number): Promise<CacheEntry | undefined> {
  await ensureLoaded()
  return cache!.get(roundCoordKey(lat, lon))
}

export async function setCachedLocation(lat: number, lon: number, entry: CacheEntry): Promise<void> {
  await ensureLoaded()
  cache!.set(roundCoordKey(lat, lon), entry)
  await persist()
}
