import { app, shell } from 'electron'
import { mkdir, readdir } from 'fs/promises'
import { join } from 'path'
import { addDays, format } from 'date-fns'
import { runIcloudpd, type IcloudpdRunHandle } from './icloudpdRunner'
import { startNewIcloudLog, appendIcloudLogLine, getCurrentIcloudLogPath } from './icloudSyncLog'
import { setLastSourceDir } from '../config/appConfig'
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
    'debug',
    '--no-progress-bar'
  ]
  if (!req.includePhotos) args.push('--skip-photos')
  if (!req.includeVideos) args.push('--skip-videos')
  if (req.dateRangeStart) args.push('--skip-created-before', req.dateRangeStart)
  if (req.dateRangeEnd) args.push('--skip-created-after', nextDay(req.dateRangeEnd))
  if (authOnly) args.push('--auth-only')
  return args
}

/**
 * icloudpd's --skip-created-after treats a bare date as that day's midnight and excludes
 * everything after it - so passing the end date as-is silently drops every asset from that
 * whole last day. Push it one day forward so the end date behaves inclusively, matching
 * dateRangeFilter.ts's `date > end-of-day` semantics for the local scan's own date range.
 */
function nextDay(dateStr: string): string {
  return format(addDays(new Date(`${dateStr}T00:00:00`), 1), 'yyyy-MM-dd')
}

let activeHandle: IcloudpdRunHandle | undefined

export function cancelSync(): void {
  activeHandle?.cancel()
}

/**
 * Runs one icloudpd invocation to completion, forwarding status updates as it goes.
 * Returns true if the process exited cleanly (code 0) and wasn't cancelled.
 */
const RECENT_LINES_BUFFER_SIZE = 15

async function runPhase(
  args: string[],
  phase: 'starting' | 'syncing',
  onStatus: (status: IcloudSyncStatusEvent) => void,
  downloadedCountRef: { count: number },
  browserOpenedRef: { opened: boolean }
): Promise<'ok' | 'cancelled' | 'error'> {
  let sawWebuiPrompt = false
  const recentLines: string[] = []

  const handle = runIcloudpd(args, (line) => {
    void appendIcloudLogLine(line)
    recentLines.push(line)
    if (recentLines.length > RECENT_LINES_BUFFER_SIZE) recentLines.shift()

    if (line.includes(AWAITING_AUTH_LOG_LINE)) {
      sawWebuiPrompt = true
      // icloudpd prints this line at the start of every invocation, including when a still-
      // valid cached cookie lets it authenticate silently a moment later - runSync makes two
      // separate icloudpd invocations (auth-only, then the real sync), so without this guard
      // the browser pops open a second, redundant tab for the second invocation every time.
      if (!browserOpenedRef.opened) {
        browserOpenedRef.opened = true
        shell.openExternal(WEBUI_URL)
      }
      onStatus({ phase: 'awaitingAuth', message: 'Sign-in page opened in your browser.' })
      return
    }

    if (line.includes(DOWNLOADING_LOG_PREFIX)) {
      downloadedCountRef.count += 1
      onStatus({ phase: 'syncing', downloadedCount: downloadedCountRef.count })
      return
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
  // A hang/crash often prints nothing containing the literal word "ERROR" before the
  // process just stops - the last several lines of context are more useful here than
  // trying to pattern-match a single "the" error line.
  const context = recentLines.length > 0 ? recentLines.join(' | ') : undefined
  onStatus({
    phase: 'error',
    error: context ?? `icloudpd exited with code ${code}`,
    logPath: getCurrentIcloudLogPath()
  })
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
  const logPath = await startNewIcloudLog()

  onStatus({ phase: 'starting', logPath })

  // Shared across both phases below so the sign-in tab opens at most once per sync run -
  // icloudpd logs the same "starting web server" line on every invocation, including when
  // a still-valid cached cookie lets it authenticate silently a moment later, so without
  // this the browser would open a second tab for phase 2 even when nothing needed signing in.
  const browserOpenedRef = { opened: false }

  // Phase 1 (--auth-only): establishes/refreshes the session.
  const authResult = await runPhase(
    buildArgs(req, cookieDir, targetDir, true),
    'starting',
    onStatus,
    { count: 0 },
    browserOpenedRef
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
    downloadedCountRef,
    browserOpenedRef
  )
  if (syncResult !== 'ok') {
    if (syncResult === 'cancelled') onStatus({ phase: 'cancelled' })
    return
  }

  const finalCount = (await readdir(targetDir)).length
  // Without this, PickDirsView's config-hydration effect (which re-reads lastSourceDir on
  // every mount) clobbers the sourceDir this sync just set with whatever stale folder was
  // persisted from an earlier session, the moment the renderer navigates back to pickDirs.
  setLastSourceDir(targetDir)
  onStatus({ phase: 'done', downloadedCount: finalCount, syncDir: targetDir, logPath })
}
