import { describe, expect, it } from 'vitest'
import { sanitizeForWindows } from './sanitize'

describe('sanitizeForWindows', () => {
  it('strips illegal Windows filename characters', () => {
    expect(sanitizeForWindows('iPhone 14 Pro: "Max"')).toBe('iPhone 14 Pro Max')
  })

  it('collapses whitespace', () => {
    expect(sanitizeForWindows('Canon   EOS   R5')).toBe('Canon EOS R5')
  })

  it('trims trailing dots and spaces', () => {
    expect(sanitizeForWindows('Some Name. . ')).toBe('Some Name')
  })

  it('leaves a clean string untouched', () => {
    expect(sanitizeForWindows('Pixel_8_Pro')).toBe('Pixel_8_Pro')
  })
})
