import { describe, expect, it } from 'vitest'
import {
  EVENT_PREFIX,
  applyLine,
  makeLineSplitter,
  parseEventLine,
  runArchiveUpdate
} from './archiveUpdate'
import { destinationFolders } from '../../shared/destinationFolders'
import type { ArchiveUpdateStatusEvent } from '../../shared/types/IpcContracts'

const ev = (o: object): string => EVENT_PREFIX + JSON.stringify(o)

describe('parseEventLine', () => {
  it('reads only lines with the prefix', () => {
    expect(parseEventLine(ev({ stage: 'scan', index: 1, total: 6 }))).toEqual({
      stage: 'scan',
      index: 1,
      total: 6
    })
    expect(parseEventLine('== 1/6  scan')).toBeUndefined()
    expect(parseEventLine(EVENT_PREFIX + '{not json')).toBeUndefined()
  })
})

describe('makeLineSplitter', () => {
  it('splits on \\n, \\r\\n and a bare \\r, across chunk boundaries', () => {
    const lines: string[] = []
    const s = makeLineSplitter((l) => lines.push(l))
    s.push('seen 256\rseen 5')
    s.push('12\r\nindexed\nfiles 4')
    s.flush()
    expect(lines).toEqual(['seen 256', 'seen 512', 'indexed', 'files 4'])
  })
})

describe('applyLine', () => {
  const start: ArchiveUpdateStatusEvent = { state: 'running' }

  it('moves through the stages and keeps the latest human line as detail', () => {
    let s = applyLine(start, ev({ stage: 'embed', index: 2, total: 6 }))
    expect(s).toMatchObject({ stage: 'embed', stageIndex: 2, stageTotal: 6 })
    s = applyLine(s, '  embedded   12 / 40')
    expect(s.detail).toBe('embedded   12 / 40')
  })

  it('turns the closing event into a summary', () => {
    const s = applyLine(
      start,
      ev({
        done: true,
        ok: true,
        published: true,
        added_roots: ['\\\\NAS\\Pictures\\2027'],
        photographs: 120,
        missing_files: 3,
        faces: 80,
        named_faces: 51
      })
    )
    expect(s.state).toBe('done')
    expect(s.summary).toEqual({
      newPhotographs: 120,
      missingFiles: 3,
      newFaces: 80,
      newlyNamedFaces: 51,
      published: true,
      addedRoots: ['\\\\NAS\\Pictures\\2027']
    })
  })

  it('ignores output that arrives after the closing event', () => {
    const done = applyLine(start, ev({ done: true, ok: true, photographs: 1 }))
    expect(applyLine(done, 'seen 1,358  new 0')).toBe(done)
  })

  it('turns a failure event into an error with its reason', () => {
    const s = applyLine(start, ev({ done: true, ok: false, failed: 'faces', error: 'faces did not finish' }))
    expect(s).toMatchObject({ state: 'error', error: 'faces did not finish' })
  })

  it('takes the stage list the run announces', () => {
    const s = applyLine(start, ev({ plan: ['scan', 'derive', 'export'] }))
    expect(s.plan).toEqual(['scan', 'derive', 'export'])
  })

  it('records new year folders announced before the stages', () => {
    const s = applyLine(start, ev({ added_roots: ['\\\\NAS\\Pictures\\2027'] }))
    expect(s.addedRoots).toEqual(['\\\\NAS\\Pictures\\2027'])
    expect(s.state).toBe('running')
  })
})

describe('runArchiveUpdate', () => {
  it('follows a real process to its summary', async () => {
    const script = [
      `const p = ${JSON.stringify(EVENT_PREFIX)};`,
      `console.log(p + JSON.stringify({added_roots: []}));`,
      `console.log(p + JSON.stringify({stage: 'scan', index: 1, total: 1}));`,
      `process.stdout.write('seen 1\\rseen 2\\n');`,
      `console.log(p + JSON.stringify({done: true, ok: true, published: false, photographs: 2, faces: 0, named_faces: 0, missing_files: 0, added_roots: []}));`
    ].join('\n')
    const seen: ArchiveUpdateStatusEvent[] = []
    const handle = runArchiveUpdate(process.execPath, ['-e', script], (s) => seen.push(s))
    const final = await handle.done

    expect(final.state).toBe('done')
    expect(final.summary?.newPhotographs).toBe(2)
    expect(seen.some((s) => s.detail === 'seen 1')).toBe(true)
  })

  it('reports the last error line when the process dies without its closing event', async () => {
    const script = `console.error('the archive does not answer'); process.exit(1)`
    const final = await runArchiveUpdate(process.execPath, ['-e', script], () => {}).done
    expect(final).toMatchObject({ state: 'error', error: 'the archive does not answer' })
  })

  it('says cancelled, not failed, when stopped', async () => {
    const handle = runArchiveUpdate(process.execPath, ['-e', 'setTimeout(() => {}, 10000)'], () => {})
    handle.cancel()
    expect((await handle.done).state).toBe('cancelled')
  })

  it('reports a program that cannot be started', async () => {
    const final = await runArchiveUpdate('C:\\nowhere\\mediaidx.exe', [], () => {}).done
    expect(final.state).toBe('error')
  })
})

describe('destinationFolders', () => {
  it('lists each folder that received files once, and none for failures', () => {
    expect(
      destinationFolders([
        { id: '1', originalPath: 'a', finalDestPath: 'Z:\\Pictures\\2026\\2026 10\\a.heic', status: 'moved' },
        { id: '2', originalPath: 'b', finalDestPath: 'Z:\\Pictures\\2026\\2026 10\\b.heic', status: 'copied' },
        { id: '3', originalPath: 'c', finalDestPath: 'Z:\\Pictures\\2026\\2026 11\\c.heic', status: 'moved' },
        { id: '4', originalPath: 'd', finalDestPath: 'Z:\\Pictures\\2026\\2026 12\\d.heic', status: 'failed' }
      ])
    ).toEqual(['Z:\\Pictures\\2026\\2026 10', 'Z:\\Pictures\\2026\\2026 11'])
  })
})
