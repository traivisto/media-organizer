import { describe, expect, it } from 'vitest'
import { existsSync } from 'fs'
import { resolveIcloudpdBinary, runIcloudpd, spawnAndPipeLines } from './icloudpdRunner'

describe('resolveIcloudpdBinary', () => {
  it('resolves to a real file bundled via the icloudpd npm package', () => {
    const bin = resolveIcloudpdBinary()
    expect(existsSync(bin)).toBe(true)
    expect(bin.toLowerCase()).toContain('icloudpd.exe')
  })
})

describe('runIcloudpd', () => {
  it('runs the real binary and line-buffers its --help output', async () => {
    const lines: string[] = []
    const handle = runIcloudpd(['--help'], (line) => lines.push(line))
    const { code, cancelled } = await handle.done

    expect(code).toBe(0)
    expect(cancelled).toBe(false)
    expect(lines.length).toBeGreaterThan(5)
    expect(lines.some((l) => l.includes('--skip-created-before'))).toBe(true)
  }, 15000)
})

describe('spawnAndPipeLines', () => {
  it('splits stdout into individual lines regardless of chunk boundaries', async () => {
    const lines: string[] = []
    const handle = spawnAndPipeLines(
      process.execPath,
      ['-e', "process.stdout.write('one\\ntwo\\nthree\\n')"],
      (line) => lines.push(line)
    )
    const { code, cancelled } = await handle.done

    expect(code).toBe(0)
    expect(cancelled).toBe(false)
    expect(lines).toEqual(['one', 'two', 'three'])
  })

  it('marks the run as cancelled when cancel() is called before the process exits', async () => {
    const handle = spawnAndPipeLines(
      process.execPath,
      ['-e', 'setTimeout(() => {}, 10000)'],
      () => {}
    )
    handle.cancel()
    const { cancelled } = await handle.done

    expect(cancelled).toBe(true)
  })
})
