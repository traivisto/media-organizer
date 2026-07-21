const ILLEGAL_CHARS = /[\\/:*?"<>|]/g

export function sanitizeForWindows(input: string): string {
  return input
    .replace(ILLEGAL_CHARS, '')
    .replace(/\s+/g, ' ')
    .trim()
    .replace(/[.\s]+$/, '')
}
