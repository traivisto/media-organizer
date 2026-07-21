import { promises as fs, constants as fsConstants } from 'fs'
import { dirname, extname, join, basename } from 'path'
import type { MoveResult, ScanRecord } from '../../shared/types/ScanRecord'

async function pathExists(path: string): Promise<boolean> {
  try {
    await fs.access(path, fsConstants.F_OK)
    return true
  } catch {
    return false
  }
}

/**
 * If destPath is already occupied (almost always a leftover from a prior run, since
 * the global sequence number makes in-batch collisions impossible), finds a free
 * "-dupN" variant instead of silently overwriting.
 */
async function resolveCollision(destPath: string): Promise<string> {
  if (!(await pathExists(destPath))) return destPath

  const dir = dirname(destPath)
  const ext = extname(destPath)
  const base = basename(destPath, ext)

  for (let n = 1; ; n++) {
    const candidate = join(dir, `${base}-dup${n}${ext}`)
    if (!(await pathExists(candidate))) return candidate
  }
}

async function moveWithExdevFallback(originalPath: string, destPath: string): Promise<void> {
  try {
    await fs.rename(originalPath, destPath)
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'EXDEV') throw err

    // Cross-volume move: copy, verify size, then delete the source. Never delete
    // before the copy is confirmed on disk.
    await fs.copyFile(originalPath, destPath)
    const [srcStat, destStat] = await Promise.all([fs.stat(originalPath), fs.stat(destPath)])
    if (srcStat.size !== destStat.size) {
      throw new Error(`Copy verification failed: size mismatch (${srcStat.size} vs ${destStat.size})`)
    }
    await fs.unlink(originalPath)
  }
}

export interface MoveEngineOptions {
  onProgress?: (processed: number, total: number, currentPath: string) => void
  onResult?: (result: MoveResult) => void
  isCancelled?: () => boolean
}

export async function executeMoves(
  records: ScanRecord[],
  options: MoveEngineOptions = {}
): Promise<MoveResult[]> {
  const results: MoveResult[] = []

  for (let i = 0; i < records.length; i++) {
    if (options.isCancelled?.()) break

    const record = records[i]
    options.onProgress?.(i, records.length, record.originalPath)

    if (!record.finalDestPath) {
      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        status: 'skipped',
        error: 'No destination path computed for this file'
      }
      results.push(result)
      options.onResult?.(result)
      continue
    }

    if (record.finalDestPath === record.originalPath) {
      // Already exactly where/what it should be (e.g. rename-in-place re-run over
      // files it already renamed correctly last time) - nothing to do, and treating
      // this as a "collision" would wrongly append -dup1 to an already-correct file.
      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: record.finalDestPath,
        status: 'moved'
      }
      results.push(result)
      options.onResult?.(result)
      continue
    }

    try {
      await fs.mkdir(dirname(record.finalDestPath), { recursive: true })
      const destPath = await resolveCollision(record.finalDestPath)
      await moveWithExdevFallback(record.originalPath, destPath)

      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: destPath,
        status: 'moved'
      }
      results.push(result)
      options.onResult?.(result)
    } catch (err) {
      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: record.finalDestPath,
        status: 'failed',
        error: err instanceof Error ? err.message : String(err)
      }
      results.push(result)
      options.onResult?.(result)
    }
  }

  return results
}
