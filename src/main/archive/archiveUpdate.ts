import { app } from 'electron'
import { spawn } from 'child_process'
import { existsSync } from 'fs'
import { appendFile, mkdir } from 'fs/promises'
import { homedir } from 'os'
import { join } from 'path'
import type {
  ArchiveUpdateStage,
  ArchiveUpdateStatusEvent,
  ArchiveUpdateSummary
} from '../../shared/types/IpcContracts'

/**
 * The second step after organizing: hand the new photos to the photo archive
 * (Kuva-arkisto, the `mediaidx` CLI in the Media classifier project). Everything it does -
 * scan, search vectors, faces, places/events, publish to the NAS - lives in `mediaidx update`;
 * this module only starts it, shows its progress and can stop it.
 *
 * `mediaidx update --events` writes ordinary human output plus one line per stage that
 * starts with EVENT_PREFIX and carries JSON. Only those lines are interpreted.
 */
export const EVENT_PREFIX = '@@mediaidx '

export function defaultMediaidxPath(): string {
  return join(homedir(), '.mediaidx', 'venv', 'Scripts', 'mediaidx.exe')
}

export function resolveMediaidx(configured?: string): string | undefined {
  const candidate = configured || defaultMediaidxPath()
  return existsSync(candidate) ? candidate : undefined
}

type MediaidxEvent = {
  plan?: ArchiveUpdateStage[]
  stage?: ArchiveUpdateStage
  index?: number
  total?: number
  done?: boolean
  ok?: boolean
  failed?: string
  error?: string
  added_roots?: string[]
  photographs?: number
  missing_files?: number
  faces?: number
  named_faces?: number
  published?: boolean
}

export function parseEventLine(line: string): MediaidxEvent | undefined {
  if (!line.startsWith(EVENT_PREFIX)) return undefined
  try {
    return JSON.parse(line.slice(EVENT_PREFIX.length)) as MediaidxEvent
  } catch {
    return undefined
  }
}

/** Splits a stream into lines on \n, \r\n *and* a bare \r: mediaidx redraws its progress
 *  lines in place with \r, and each redraw should show up as it happens. */
export function makeLineSplitter(onLine: (line: string) => void): {
  push: (chunk: string) => void
  flush: () => void
} {
  let buffer = ''
  return {
    push(chunk) {
      buffer += chunk
      const parts = buffer.split(/\r\n|\r|\n/)
      buffer = parts.pop() ?? ''
      for (const p of parts) if (p.trim().length > 0) onLine(p)
    },
    flush() {
      if (buffer.trim().length > 0) onLine(buffer)
      buffer = ''
    }
  }
}

/** Folds one output line into the status shown on screen. Pure, so it can be tested
 *  without a process. */
export function applyLine(
  status: ArchiveUpdateStatusEvent,
  line: string
): ArchiveUpdateStatusEvent {
  const event = parseEventLine(line)
  // stderr is read separately from stdout, so a progress line can arrive after the closing
  // event; once finished, the result stands.
  if (status.state !== 'running') return status
  if (!event) return { ...status, detail: line.trim() }

  if (event.added_roots && !event.done) return { ...status, addedRoots: event.added_roots }
  if (event.plan) return { ...status, plan: event.plan }
  if (event.stage) {
    return {
      ...status,
      stage: event.stage,
      stageIndex: event.index,
      stageTotal: event.total,
      detail: undefined
    }
  }
  if (event.done) {
    if (!event.ok) {
      return { ...status, state: 'error', error: event.error ?? `${event.failed} did not finish` }
    }
    const summary: ArchiveUpdateSummary = {
      newPhotographs: event.photographs ?? 0,
      missingFiles: event.missing_files ?? 0,
      newFaces: event.faces ?? 0,
      newlyNamedFaces: event.named_faces ?? 0,
      published: !!event.published,
      addedRoots: event.added_roots ?? []
    }
    return { ...status, state: 'done', summary, detail: undefined }
  }
  return status
}

export interface ArchiveUpdateHandle {
  cancel: () => void
  done: Promise<ArchiveUpdateStatusEvent>
}

export function runArchiveUpdate(
  bin: string,
  args: string[],
  onStatus: (status: ArchiveUpdateStatusEvent) => void,
  logPath?: string
): ArchiveUpdateHandle {
  let status: ArchiveUpdateStatusEvent = { state: 'running', logPath }
  onStatus(status)

  const child = spawn(bin, args, {
    stdio: ['ignore', 'pipe', 'pipe'],
    windowsHide: true,
    // Python buffers a piped stdout; without this the stages arrive all at once at the end.
    env: { ...process.env, PYTHONUNBUFFERED: '1', PYTHONIOENCODING: 'utf-8' }
  })
  let cancelled = false
  let lastErrorLine: string | undefined

  const handleLine = (line: string, fromStderr: boolean): void => {
    if (logPath) void appendFile(logPath, line + '\n', 'utf-8').catch(() => {})
    if (fromStderr && !line.startsWith(EVENT_PREFIX)) lastErrorLine = line.trim()
    status = applyLine(status, line)
    onStatus(status)
  }
  const out = makeLineSplitter((l) => handleLine(l, false))
  const err = makeLineSplitter((l) => handleLine(l, true))
  child.stdout?.on('data', (c: Buffer) => out.push(c.toString('utf-8')))
  child.stderr?.on('data', (c: Buffer) => err.push(c.toString('utf-8')))

  const done = new Promise<ArchiveUpdateStatusEvent>((resolve) => {
    const finish = (code: number | null, spawnError?: string): void => {
      out.flush()
      err.flush()
      if (cancelled) {
        status = { ...status, state: 'cancelled' }
      } else if (spawnError) {
        status = { ...status, state: 'error', error: spawnError }
      } else if (status.state === 'running') {
        // Exited without its closing event: a crash, or killed from outside.
        status = {
          ...status,
          state: code === 0 ? 'done' : 'error',
          error: code === 0 ? undefined : (lastErrorLine ?? `mediaidx exited with code ${code}`)
        }
      }
      onStatus(status)
      resolve(status)
    }
    child.on('error', (e) => finish(null, e.message))
    child.on('close', (code) => finish(code))
  })

  return {
    cancel: () => {
      cancelled = true
      // mediaidx.exe is a launcher that starts python.exe; killing only the launcher would
      // leave the real work running. /T takes the whole tree.
      if (child.pid !== undefined && process.platform === 'win32') {
        spawn('taskkill', ['/pid', String(child.pid), '/T', '/F'], { windowsHide: true })
      } else {
        child.kill()
      }
    },
    done
  }
}

let active: ArchiveUpdateHandle | undefined

export async function startArchiveUpdate(
  configuredPath: string | undefined,
  onStatus: (status: ArchiveUpdateStatusEvent) => void
): Promise<void> {
  if (active) return
  const bin = resolveMediaidx(configuredPath)
  if (!bin) {
    onStatus({
      state: 'error',
      error:
        `The photo archive program was not found at ${configuredPath || defaultMediaidxPath()}. ` +
        'Set "mediaidxPath" in media-organizer-config.json if it is installed elsewhere.'
    })
    return
  }
  const dir = join(app.getPath('userData'), 'archive-update-logs')
  await mkdir(dir, { recursive: true })
  const logPath = join(dir, `run-${new Date().toISOString().replace(/[:.]/g, '-')}.log`)

  active = runArchiveUpdate(bin, ['update', '--events'], onStatus, logPath)
  try {
    await active.done
  } finally {
    active = undefined
  }
}

export function cancelArchiveUpdate(): void {
  active?.cancel()
}
