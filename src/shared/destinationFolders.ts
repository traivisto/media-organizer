import type { MoveResult } from './types/ScanRecord'

/** The distinct folders a batch put files into, so they can be looked through. */
export function destinationFolders(results: MoveResult[]): string[] {
  const dirs = new Set<string>()
  for (const r of results) {
    if ((r.status === 'moved' || r.status === 'copied') && r.finalDestPath) {
      const cut = Math.max(r.finalDestPath.lastIndexOf('\\'), r.finalDestPath.lastIndexOf('/'))
      if (cut > 0) dirs.add(r.finalDestPath.slice(0, cut))
    }
  }
  return [...dirs].sort()
}
