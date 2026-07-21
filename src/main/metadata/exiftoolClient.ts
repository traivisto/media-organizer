import { ExifTool, type Tags } from 'exiftool-vendored'

let pool: ExifTool | undefined

export function initExiftoolPool(): void {
  if (!pool) {
    pool = new ExifTool({ maxProcs: 2 })
  }
}

export async function shutdownExiftoolPool(): Promise<void> {
  if (pool) {
    await pool.end()
    pool = undefined
  }
}

export interface ExiftoolReadResult {
  tags?: Tags
  error?: string
}

export async function readTags(filePath: string): Promise<ExiftoolReadResult> {
  if (!pool) initExiftoolPool()
  try {
    const tags = await pool!.read(filePath)
    return { tags }
  } catch (err) {
    return { error: err instanceof Error ? err.message : String(err) }
  }
}
