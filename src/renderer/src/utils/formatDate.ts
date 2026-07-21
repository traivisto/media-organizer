import { format } from 'date-fns'

/**
 * Formats a date using this Windows PC's actual regional date pattern when available
 * (read from the registry in the main process - see windowsRegionalFormat.ts), falling
 * back to the runtime's default locale formatting otherwise (e.g. non-Windows, or if
 * the pattern turns out to be unparseable).
 */
export function formatSystemDate(date: Date, datePattern: string | undefined): string {
  if (datePattern) {
    try {
      return format(date, datePattern)
    } catch {
      // fall through to the locale-default formatting below
    }
  }
  return date.toLocaleDateString()
}

export function formatSystemDateTime(
  date: Date,
  datePattern: string | undefined,
  timePattern: string | undefined
): string {
  if (datePattern && timePattern) {
    try {
      return format(date, `${datePattern} ${timePattern}`)
    } catch {
      // fall through to the locale-default formatting below
    }
  }
  return date.toLocaleString()
}
