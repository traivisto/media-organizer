import { afterAll, afterEach, describe, expect, it, vi } from 'vitest'
import { mkdtemp, rm } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'

const testUserDataDir = await mkdtemp(join(tmpdir(), 'media-organizer-geocache-'))

vi.mock('electron', () => ({
  app: { getPath: () => testUserDataDir }
}))

const reverseGeocodeMock = vi.fn()
vi.mock('./nominatimClient', () => ({
  reverseGeocode: (...args: [number, number]) => reverseGeocodeMock(...args)
}))

const { geocodeCoords } = await import('./geocodeService')

describe('geocodeCoords', () => {
  afterEach(() => {
    reverseGeocodeMock.mockReset()
  })

  afterAll(async () => {
    await rm(testUserDataDir, { recursive: true, force: true })
  })

  it('hits the network once and caches the result for a repeat lookup', async () => {
    reverseGeocodeMock.mockResolvedValue('Helsinki')

    expect(await geocodeCoords(60.1699, 24.9384)).toBe('Helsinki')
    expect(await geocodeCoords(60.16988, 24.93841)).toBe('Helsinki') // same ~111m cell

    expect(reverseGeocodeMock).toHaveBeenCalledTimes(1)
  })

  it('dedups concurrent in-flight lookups for the same coordinates', async () => {
    reverseGeocodeMock.mockResolvedValue('Tampere')

    const [a, b] = await Promise.all([geocodeCoords(61.4978, 23.761), geocodeCoords(61.4978, 23.761)])

    expect(a).toBe('Tampere')
    expect(b).toBe('Tampere')
    expect(reverseGeocodeMock).toHaveBeenCalledTimes(1)
  })

  it('resolves to undefined (never throws) when the geocode call fails', async () => {
    reverseGeocodeMock.mockRejectedValue(new Error('network down'))
    await expect(geocodeCoords(1.111, 2.222)).resolves.toBeUndefined()
  })

  it('caches negative results (not found) too, without re-hitting the network', async () => {
    reverseGeocodeMock.mockResolvedValue(undefined)

    expect(await geocodeCoords(0.001, 0.001)).toBeUndefined()
    expect(await geocodeCoords(0.0012, 0.0011)).toBeUndefined()

    expect(reverseGeocodeMock).toHaveBeenCalledTimes(1)
  })
})
