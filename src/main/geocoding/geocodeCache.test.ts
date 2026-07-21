import { describe, expect, it } from 'vitest'
import { roundCoordKey } from './geocodeCache'

describe('roundCoordKey', () => {
  it('rounds to 3 decimal places (~111m)', () => {
    expect(roundCoordKey(60.169857, 24.938379)).toBe('60.170,24.938')
  })

  it('produces the same key for coordinates within the same ~111m cell', () => {
    expect(roundCoordKey(60.1698, 24.9384)).toBe(roundCoordKey(60.16977, 24.93842))
  })

  it('produces different keys for coordinates in different cells', () => {
    expect(roundCoordKey(60.1698, 24.9384)).not.toBe(roundCoordKey(60.1712, 24.9401))
  })

  it('handles negative coordinates', () => {
    expect(roundCoordKey(-33.868857, 151.209295)).toBe('-33.869,151.209')
  })
})
