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

async function copyAndVerify(originalPath: string, destPath: string): Promise<void> {
  await fs.copyFile(originalPath, destPath)
  const [srcStat, destStat] = await Promise.all([fs.stat(originalPath), fs.stat(destPath)])
  if (srcStat.size !== destStat.size) {
    throw new Error(`Copy verification failed: size mismatch (${srcStat.size} vs ${destStat.size})`)
  }
}

async function moveWithExdevFallback(originalPath: string, destPath: string): Promise<void> {
  try {
    await fs.rename(originalPath, destPath)
  } catch (err) {
    if ((err as NodeJS.ErrnoException).code !== 'EXDEV') throw err

    // Cross-volume move: copy, verify size, then delete the source. Never delete
    // before the copy is confirmed on disk.
    await copyAndVerify(originalPath, destPath)
    await fs.unlink(originalPath)
  }
}

export type ExecutionMode = 'move' | 'copy'

export interface MoveEngineOptions {
  mode?: ExecutionMode
  onProgress?: (processed: number, total: number, currentPath: string) => void
  onResult?: (result: MoveResult) => void
  isCancelled?: () => boolean
  /** Re-sequencing an already-organized folder can require two files to swap places
   *  (file A's new name is file B's current name and vice versa). Renaming straight to
   *  finalDestPath one at a time would misdetect that swap as a real collision and
   *  suffix it with -dupN. When true, every record is first renamed to a unique temp
   *  name in its own original folder, then all temp names are renamed to their final
   *  destination - so no rename ever collides with a file still awaiting its own move. */
  twoPhase?: boolean
}

export async function executeMoves(
  records: ScanRecord[],
  options: MoveEngineOptions = {}
): Promise<MoveResult[]> {
  if (options.twoPhase) {
    return executeMovesTwoPhase(records, options)
  }

  const mode = options.mode ?? 'move'
  const doneStatus = mode === 'copy' ? 'copied' : 'moved'
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
        status: doneStatus
      }
      results.push(result)
      options.onResult?.(result)
      continue
    }

    try {
      await fs.mkdir(dirname(record.finalDestPath), { recursive: true })
      const destPath = await resolveCollision(record.finalDestPath)
      if (mode === 'copy') {
        await copyAndVerify(record.originalPath, destPath)
      } else {
        await moveWithExdevFallback(record.originalPath, destPath)
      }

      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: destPath,
        status: doneStatus
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

interface StagedMove {
  record: ScanRecord
  tempPath: string
}

async function executeMovesTwoPhase(
  records: ScanRecord[],
  options: MoveEngineOptions
): Promise<MoveResult[]> {
  const mode = options.mode ?? 'move'
  const doneStatus = mode === 'copy' ? 'copied' : 'moved'
  const results: MoveResult[] = []
  const staged: StagedMove[] = []

  // Phase 1: stage every real move under a unique temp name in its own original folder.
  // Cancellation only stops further files from being staged here - anything already
  // staged still gets driven to its final name in phase 2 below, so nothing is left
  // behind under a temp name.
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
      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: record.finalDestPath,
        status: doneStatus
      }
      results.push(result)
      options.onResult?.(result)
      continue
    }

    try {
      const tempPath = join(dirname(record.originalPath), `.mo-tmp-${record.id}${extname(record.originalPath)}`)
      if (mode === 'copy') {
        await copyAndVerify(record.originalPath, tempPath)
      } else {
        await moveWithExdevFallback(record.originalPath, tempPath)
      }
      staged.push({ record, tempPath })
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

  // Phase 2: every staged temp file already sits under a name nothing else in this
  // batch is still using, so renaming it into its final spot can never collide with a
  // file still awaiting its own move.
  for (const { record, tempPath } of staged) {
    try {
      await fs.mkdir(dirname(record.finalDestPath!), { recursive: true })
      const destPath = await resolveCollision(record.finalDestPath!)
      await fs.rename(tempPath, destPath)

      const result: MoveResult = {
        id: record.id,
        originalPath: record.originalPath,
        finalDestPath: destPath,
        status: doneStatus
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
