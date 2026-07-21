import { app } from 'electron'
import { promises as fs } from 'fs'
import { join } from 'path'
import type { MoveResult } from '../../shared/types/ScanRecord'

let currentLogPath: string | undefined

export async function startNewLog(): Promise<string> {
  const dir = join(app.getPath('userData'), 'operations-logs')
  await fs.mkdir(dir, { recursive: true })
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  currentLogPath = join(dir, `run-${timestamp}.jsonl`)
  return currentLogPath
}

export async function appendLogEntry(entry: MoveResult): Promise<void> {
  if (!currentLogPath) return
  await fs.appendFile(currentLogPath, `${JSON.stringify(entry)}\n`, 'utf-8')
}

export function getCurrentLogPath(): string | undefined {
  return currentLogPath
}
