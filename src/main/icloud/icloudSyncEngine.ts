import { app, shell } from 'electron'
import { mkdir, readdir } from 'fs/promises'
import { join } from 'path'
import { runIcloudpd, type IcloudpdRunHandle } from './icloudpdRunner'
import type { IcloudSyncStartRequest, IcloudSyncStatusEvent } from '../../shared/types/IpcContracts'

const WEBUI_URL = 'http://127.0.0.1:8080'
const AWAITING_AUTH_LOG_LINE = 'Starting web server for WebUI authentication'
const DOWNLOADING_LOG_PREFIX = 'Downloading '

function getCookieDir(): string {
  return join(app.getPath('userData'), 'icloud-session')
}

export function getSyncCacheDir(): string {
  return join(app.getPath('userData'), 'icloud-photos-cache')
}

export function buildArgs(
  req: IcloudSyncStartRequest,
  cookieDir: string,
  targetDir: string,
  authOnly: boolean
): string[] {
  const args = [
    '--username',
    req.appleId,
    '--cookie-directory',
    cookieDir,
    '--directory',
    targetDir,
    '--password-provider',
    'webui',
    '--mfa-provider',
    'webui',
    '--folder-structure',
    'none',
    '--log-level',
    'info',
    '--no-progress-bar'
  ]
  if (!req.includePhotos) args.push('--skip-photos')
  if (!req.includeVideos) args.push('--skip-videos')
  if (req.dateRangeStart) args.push('--skip-created-before', req.dateRangeStart)
  if (req.dateRangeEnd) args.push('--skip-created-after', req.dateRangeEnd)
  if (authOnly) args.push('--auth-only')
  return args
}

let activeHandle: IcloudpdRunHandle | undefined

export function cancelSync(): void {
  activeHandle?.cancel()
}

/**
 * Runs one icloudpd invocation to completion, forwarding status updates as it goes.
 * Returns true if the process exited cleanly (code 0) and wasn't cancelled.
 */
async function runPhase(
  args: string[],
  phase: 'starting' | 'syncing',
  onStatus: (status: IcloudSyncStatusEvent) => void,
  downloadedCountRef: { count: number }
): Promise<'ok' | 'cancelled' | 'error'> {
  let sawWebuiPrompt = false
  let lastErrorLine: string | undefined

  const handle = runIcloudpd(args, (line) => {
    if (line.includes(AWAITING_AUTH_LOG_LINE)) {
      sawWebuiPrompt = true
      shell.openExternal(WEBUI_URL)
      onStatus({ phase: 'awaitingAuth', message: 'Sign-in page opened in your browser.' })
      return
    }

    if (line.includes(DOWNLOADING_LOG_PREFIX)) {
      downloadedCountRef.count += 1
      onStatus({ phase: 'syncing', downloadedCount: downloadedCountRef.count })
      return
    }

    if (/\bERROR\b/.test(line)) {
      lastErrorLine = line
    }

    // Best-effort visibility into what icloudpd is doing right now (including Apple-side
    // rejections during a webui sign-in attempt, which don't make the process exit - it
    // just loops back to re-serving the sign-in form for another try).
    onStatus({ phase: sawWebuiPrompt ? 'awaitingAuth' : phase, message: line })
  })

  activeHandle = handle
  const { code, cancelled } = await handle.done
  activeHandle = undefined

  if (cancelled) return 'cancelled'
  if (code === 0) return 'ok'
  onStatus({ phase: 'error', error: lastErrorLine ?? `icloudpd exited with code ${code}` })
  return 'error'
}

export async function runSync(
  req: IcloudSyncStartRequest,
  onStatus: (status: IcloudSyncStatusEvent) => void
): Promise<void> {
  const cookieDir = getCookieDir()
  const targetDir = getSyncCacheDir()
  await mkdir(cookieDir, { recursive: true })
  await mkdir(targetDir, { recursive: true })

  onStatus({ phase: 'starting' })

  // Phase 1 (--auth-only): establishes/refreshes the session. Only opens the sign-in
  // page if the session cached in cookieDir from a previous run has expired or doesn't
  // exist yet - if it's still valid, this phase completes silently with no browser popup.
  const authResult = await runPhase(
    buildArgs(req, cookieDir, targetDir, true),
    'starting',
    onStatus,
    { count: 0 }
  )
  if (authResult !== 'ok') {
    if (authResult === 'cancelled') onStatus({ phase: 'cancelled' })
    return
  }

  onStatus({ phase: 'authenticated' })

  // Phase 2: the actual download, reusing the now-fresh session from phase 1.
  const downloadedCountRef = { count: 0 }
  const syncResult = await runPhase(
    buildArgs(req, cookieDir, targetDir, false),
    'syncing',
    onStatus,
    downloadedCountRef
  )
  if (syncResult !== 'ok') {
    if (syncResult === 'cancelled') onStatus({ phase: 'cancelled' })
    return
  }

  const finalCount = (await readdir(targetDir)).length
  onStatus({ phase: 'done', downloadedCount: finalCount, syncDir: targetDir })
}
