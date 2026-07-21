import { createRequire } from 'module'
import { spawn } from 'child_process'
import { platform, arch } from 'os'
import { sep } from 'path'

const nodeRequire = createRequire(import.meta.url)

/**
 * icloudpd ships as a platform-specific prebuilt binary (npm package `@icloudpd/<platform>-<arch>`,
 * pulled in as an optionalDependency of `icloudpd`). This app only targets 64-bit Windows
 * (see electron-builder.yml), so only that one platform is resolved.
 */
export function resolveIcloudpdBinary(): string {
  if (platform() !== 'win32' || arch() !== 'x64') {
    throw new Error('iCloud sync is only supported on 64-bit Windows in this build.')
  }
  const resolved = nodeRequire.resolve('@icloudpd/win32-x64/bin/icloudpd.exe')
  // In a packaged build, require.resolve() returns a path *inside* app.asar - fine for
  // require()'d JS, but child_process.spawn() can't execute a binary from inside the asar
  // archive. electron-builder unpacks binaries like this one into an app.asar.unpacked
  // mirror alongside it; redirect there, the same fix exiftool-vendored's own resolver
  // applies for its bundled binary.
  return resolved.replace(`${sep}app.asar${sep}`, `${sep}app.asar.unpacked${sep}`)
}

export interface IcloudpdRunHandle {
  cancel: () => void
  /** Resolves when the process exits. `cancelled` is true if cancel() caused the exit. */
  done: Promise<{ code: number | null; cancelled: boolean }>
}

/**
 * Spawns an arbitrary executable with stdio piped (not inherited), line-buffering its
 * combined stdout/stderr output. Split out from runIcloudpd so the process-management
 * and line-buffering behavior can be unit tested against a trivial command, without
 * needing the real icloudpd binary (which requires network/Apple auth to do anything).
 */
export function spawnAndPipeLines(
  bin: string,
  args: string[],
  onLine: (line: string) => void
): IcloudpdRunHandle {
  const child = spawn(bin, args, { stdio: ['ignore', 'pipe', 'pipe'] })
  let cancelled = false

  function pipeLines(stream: NodeJS.ReadableStream): void {
    let buffer = ''
    stream.on('data', (chunk: Buffer) => {
      buffer += chunk.toString('utf-8')
      const lines = buffer.split(/\r?\n/)
      buffer = lines.pop() ?? ''
      for (const line of lines) {
        if (line.length > 0) onLine(line)
      }
    })
  }

  if (child.stdout) pipeLines(child.stdout)
  if (child.stderr) pipeLines(child.stderr)

  const done = new Promise<{ code: number | null; cancelled: boolean }>((resolve) => {
    child.on('close', (code) => resolve({ code, cancelled }))
  })

  return {
    cancel: () => {
      cancelled = true
      child.kill()
    },
    done
  }
}

/**
 * Spawns icloudpd with stdio piped (not inherited) so every log line can be inspected
 * here, rather than relying on the console/TTY prompts icloudpd normally expects.
 */
export function runIcloudpd(args: string[], onLine: (line: string) => void): IcloudpdRunHandle {
  return spawnAndPipeLines(resolveIcloudpdBinary(), args, onLine)
}
