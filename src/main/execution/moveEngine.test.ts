import { afterEach, describe, expect, it, vi } from 'vitest'
import { promises as fs } from 'fs'
import { mkdtemp, rm, writeFile, readFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { executeMoves } from './moveEngine'
import type { ScanRecord } from '../../shared/types/ScanRecord'

function baseRecord(overrides: Partial<ScanRecord> = {}): ScanRecord {
  return {
    id: overrides.id ?? '1',
    originalPath: '',
    mediaKind: 'photo',
    extension: '.jpg',
    fileSize: 10,
    mtime: '2026-01-01T00:00:00',
    metadataStatus: 'ok',
    needsResolution: false,
    ...overrides
  }
}

describe('executeMoves', () => {
  afterEach(() => {
    vi.restoreAllMocks()
  })

  it('moves a file into a newly created destination folder', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'source', 'photo.jpg')
      await fs.mkdir(join(dir, 'source'), { recursive: true })
      await writeFile(src, 'hello')
      const dest = join(dir, 'dest', '2026', '01', 'photo.jpg')

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: dest })])

      expect(results).toHaveLength(1)
      expect(results[0].status).toBe('moved')
      expect(results[0].finalDestPath).toBe(dest)
      expect(await readFile(dest, 'utf-8')).toBe('hello')
      await expect(fs.access(src)).rejects.toThrow()
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('suffixes with -dupN instead of overwriting when the destination already exists', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'photo.jpg')
      await writeFile(src, 'new content')
      const dest = join(dir, 'dest', 'photo.jpg')
      await fs.mkdir(join(dir, 'dest'), { recursive: true })
      await writeFile(dest, 'existing content - must not be overwritten')

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: dest })])

      expect(results[0].status).toBe('moved')
      expect(results[0].finalDestPath).toBe(join(dir, 'dest', 'photo-dup1.jpg'))
      expect(await readFile(dest, 'utf-8')).toBe('existing content - must not be overwritten')
      expect(await readFile(join(dir, 'dest', 'photo-dup1.jpg'), 'utf-8')).toBe('new content')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('leaves a file untouched (no false -dupN) when finalDestPath equals originalPath', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'already-correct.jpg')
      await writeFile(src, 'unchanged content')

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: src })])

      expect(results[0].status).toBe('moved')
      expect(results[0].finalDestPath).toBe(src)
      expect(await readFile(src, 'utf-8')).toBe('unchanged content')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('records a failure and continues processing subsequent files when a move fails', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const missingSrc = join(dir, 'does-not-exist.jpg')
      const okSrc = join(dir, 'ok.jpg')
      await writeFile(okSrc, 'ok')

      const results = await executeMoves([
        baseRecord({ id: 'a', originalPath: missingSrc, finalDestPath: join(dir, 'dest', 'a.jpg') }),
        baseRecord({ id: 'b', originalPath: okSrc, finalDestPath: join(dir, 'dest', 'b.jpg') })
      ])

      expect(results[0].status).toBe('failed')
      expect(results[0].error).toBeTruthy()
      expect(results[1].status).toBe('moved')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('falls back to copy+verify+unlink when rename fails with EXDEV', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'photo.jpg')
      await writeFile(src, 'cross volume content')
      const dest = join(dir, 'dest', 'photo.jpg')

      const renameSpy = vi
        .spyOn(fs, 'rename')
        .mockRejectedValueOnce(Object.assign(new Error('cross-device link'), { code: 'EXDEV' }))

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: dest })])

      expect(renameSpy).toHaveBeenCalled()
      expect(results[0].status).toBe('moved')
      expect(await readFile(dest, 'utf-8')).toBe('cross volume content')
      await expect(fs.access(src)).rejects.toThrow()
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('copies a file and leaves the source untouched when mode is "copy"', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'source', 'photo.jpg')
      await fs.mkdir(join(dir, 'source'), { recursive: true })
      await writeFile(src, 'hello')
      const dest = join(dir, 'dest', '2026', '01', 'photo.jpg')

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: dest })], {
        mode: 'copy'
      })

      expect(results[0].status).toBe('copied')
      expect(results[0].finalDestPath).toBe(dest)
      expect(await readFile(dest, 'utf-8')).toBe('hello')
      expect(await readFile(src, 'utf-8')).toBe('hello')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('suffixes with -dupN instead of overwriting in copy mode too', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src = join(dir, 'photo.jpg')
      await writeFile(src, 'new content')
      const dest = join(dir, 'dest', 'photo.jpg')
      await fs.mkdir(join(dir, 'dest'), { recursive: true })
      await writeFile(dest, 'existing content - must not be overwritten')

      const results = await executeMoves([baseRecord({ originalPath: src, finalDestPath: dest })], {
        mode: 'copy'
      })

      expect(results[0].status).toBe('copied')
      expect(results[0].finalDestPath).toBe(join(dir, 'dest', 'photo-dup1.jpg'))
      expect(await readFile(dest, 'utf-8')).toBe('existing content - must not be overwritten')
      expect(await readFile(join(dir, 'dest', 'photo-dup1.jpg'), 'utf-8')).toBe('new content')
      expect(await readFile(src, 'utf-8')).toBe('new content')
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })

  it('stops processing further files once cancelled', async () => {
    const dir = await mkdtemp(join(tmpdir(), 'media-organizer-move-'))
    try {
      const src1 = join(dir, 'a.jpg')
      const src2 = join(dir, 'b.jpg')
      await writeFile(src1, 'a')
      await writeFile(src2, 'b')

      const results = await executeMoves(
        [
          baseRecord({ id: 'a', originalPath: src1, finalDestPath: join(dir, 'dest', 'a.jpg') }),
          baseRecord({ id: 'b', originalPath: src2, finalDestPath: join(dir, 'dest', 'b.jpg') })
        ],
        { isCancelled: () => true }
      )

      expect(results).toHaveLength(0)
    } finally {
      await rm(dir, { recursive: true, force: true })
    }
  })
})
