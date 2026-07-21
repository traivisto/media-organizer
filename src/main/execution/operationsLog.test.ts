import { afterAll, describe, expect, it, vi } from 'vitest'
import { mkdtemp, rm, readFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'

const testUserDataDir = await mkdtemp(join(tmpdir(), 'media-organizer-oplog-'))

vi.mock('electron', () => ({
  app: { getPath: () => testUserDataDir }
}))

const { startNewLog, appendLogEntry, getCurrentLogPath } = await import('./operationsLog')

describe('operationsLog', () => {
  afterAll(async () => {
    await rm(testUserDataDir, { recursive: true, force: true })
  })

  it('creates a new log file and appends JSONL entries incrementally', async () => {
    const logPath = await startNewLog()
    expect(getCurrentLogPath()).toBe(logPath)

    await appendLogEntry({ id: '1', originalPath: 'a.jpg', status: 'moved', finalDestPath: 'dest/a.jpg' })
    await appendLogEntry({ id: '2', originalPath: 'b.jpg', status: 'failed', error: 'boom' })

    const content = await readFile(logPath, 'utf-8')
    const lines = content.trim().split('\n').map((l) => JSON.parse(l))

    expect(lines).toHaveLength(2)
    expect(lines[0]).toMatchObject({ id: '1', status: 'moved' })
    expect(lines[1]).toMatchObject({ id: '2', status: 'failed', error: 'boom' })
  })
})
