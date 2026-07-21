import { getCachedLocation, roundCoordKey, setCachedLocation } from './geocodeCache'
import { reverseGeocode } from './nominatimClient'
import { enqueueGeocodeRequest } from './rateLimiter'

const inFlight = new Map<string, Promise<string | undefined>>()

/**
 * Resolves GPS coordinates to a place name: dedups identical in-flight lookups (rounded
 * to ~111m), checks the persistent cache (including negative results), and falls back to
 * a rate-limited Nominatim call on a miss. Never throws - geocode failures just resolve
 * to undefined so they never block the rest of the pipeline.
 */
export async function geocodeCoords(lat: number, lon: number): Promise<string | undefined> {
  const key = roundCoordKey(lat, lon)

  const existing = inFlight.get(key)
  if (existing) return existing

  const promise = (async () => {
    try {
      const cached = await getCachedLocation(lat, lon)
      if (cached) return cached.location ?? undefined

      const location = await enqueueGeocodeRequest(() => reverseGeocode(lat, lon))
      await setCachedLocation(lat, lon, { location: location ?? null })
      return location
    } catch {
      return undefined
    } finally {
      inFlight.delete(key)
    }
  })()

  inFlight.set(key, promise)
  return promise
}
