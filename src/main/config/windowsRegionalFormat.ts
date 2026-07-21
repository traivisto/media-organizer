import { execFile } from 'child_process'
import { promisify } from 'util'

const execFileAsync = promisify(execFile)

export interface WindowsDateTimeFormat {
  datePattern: string
  timePattern: string
}

/**
 * Translates a Windows short date/time format string (from the registry, e.g.
 * "M/d/yyyy" or "h:mm:ss tt") into the equivalent date-fns format token string.
 * Windows and date-fns tokens are nearly identical (yyyy, MM, dd, HH, hh, mm, ss);
 * the only real differences are day names (Windows dddd/ddd vs date-fns EEEE/EEE)
 * and the AM/PM marker (Windows tt/t vs date-fns aa).
 */
export function windowsPatternToDateFns(pattern: string): string {
  return pattern
    .replace(/dddd/g, 'EEEE')
    .replace(/ddd/g, 'EEE')
    .replace(/tt/g, 'aa')
    .replace(/(?<![a-zA-Z])t(?![a-zA-Z])/g, 'aa')
}

async function readRegValue(valueName: string): Promise<string | undefined> {
  try {
    const { stdout } = await execFileAsync('reg', [
      'query',
      'HKCU\\Control Panel\\International',
      '/v',
      valueName
    ])
    const match = stdout.match(/REG_SZ\s+(.+)/)
    return match?.[1]?.trim()
  } catch {
    return undefined
  }
}

let cached: WindowsDateTimeFormat | undefined | null = null

/**
 * Reads this Windows user's actual regional date/time format (the same source
 * Explorer and other native apps use for "short date"/"short time"), rather than
 * relying on Intl/ICU locale resolution, which does not reflect Windows' custom
 * per-field format overrides. Returns undefined on non-Windows platforms or if the
 * registry read fails, so callers can fall back to the runtime's default formatting.
 */
export async function getWindowsDateTimeFormat(): Promise<WindowsDateTimeFormat | undefined> {
  if (cached !== null) return cached ?? undefined
  if (process.platform !== 'win32') {
    cached = undefined
    return undefined
  }

  const [shortDate, shortTime, timeFormat] = await Promise.all([
    readRegValue('sShortDate'),
    readRegValue('sShortTime'),
    readRegValue('sTimeFormat')
  ])

  const rawTimePattern = shortTime ?? timeFormat
  if (!shortDate || !rawTimePattern) {
    cached = undefined
    return undefined
  }

  cached = {
    datePattern: windowsPatternToDateFns(shortDate),
    timePattern: windowsPatternToDateFns(rawTimePattern)
  }
  return cached
}
