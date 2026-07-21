import { app } from 'electron'
import { promises as fs } from 'fs'
import { join } from 'path'

let currentLogPath: string | undefined

/**
 * Persists icloudpd's full raw output to disk for one sync run, so a silent crash/hang
 * (which prints nothing that looks like "ERROR" but just stops) can still be diagnosed
 * after the fact - the webui only shows a live view that's gone once the process exits.
 */
export async function startNewIcloudLog(): Promise<string> {
  const dir = join(app.getPath('userData'), 'icloud-sync-logs')
  await fs.mkdir(dir, { recursive: true })
  const timestamp = new Date().toISOString().replace(/[:.]/g, '-')
  currentLogPath = join(dir, `run-${timestamp}.log`)
  return currentLogPath
}

export async function appendIcloudLogLine(line: string): Promise<void> {
  if (!currentLogPath) return
  await fs.appendFile(currentLogPath, `${line}\n`, 'utf-8')
}

export function getCurrentIcloudLogPath(): string | undefined {
  return currentLogPath
}
