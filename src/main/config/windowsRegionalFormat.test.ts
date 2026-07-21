import { describe, expect, it } from 'vitest'
import { windowsPatternToDateFns } from './windowsRegionalFormat'

describe('windowsPatternToDateFns', () => {
  it('passes through common short date patterns unchanged (tokens already match date-fns)', () => {
    expect(windowsPatternToDateFns('M/d/yyyy')).toBe('M/d/yyyy')
    expect(windowsPatternToDateFns('dd.MM.yyyy')).toBe('dd.MM.yyyy')
    expect(windowsPatternToDateFns('yyyy-MM-dd')).toBe('yyyy-MM-dd')
  })

  it('translates AM/PM marker tt to date-fns aa', () => {
    expect(windowsPatternToDateFns('h:mm:ss tt')).toBe('h:mm:ss aa')
    expect(windowsPatternToDateFns('h:mm tt')).toBe('h:mm aa')
  })

  it('translates 24-hour time patterns unchanged', () => {
    expect(windowsPatternToDateFns('HH:mm:ss')).toBe('HH:mm:ss')
    expect(windowsPatternToDateFns('HH:mm')).toBe('HH:mm')
  })

  it('translates long date day-name tokens', () => {
    expect(windowsPatternToDateFns('dddd, MMMM d, yyyy')).toBe('EEEE, MMMM d, yyyy')
  })
})
