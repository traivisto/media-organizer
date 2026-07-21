import { afterAll, beforeAll, describe, expect, it } from 'vitest'
import { mkdtemp, rm, writeFile, utimes } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { ExifTool } from 'exiftool-vendored'
import { walkDirectory } from '../scanning/fileWalker'
import { readTags, shutdownExiftoolPool, initExiftoolPool } from '../metadata/exiftoolClient'
import { normalizeMetadata } from '../metadata/metadataNormalizer'
import { assignSequenceNumbers } from '../naming/sequencer'
import { buildFileName } from '../naming/filenameBuilder'
import { buildDestPath } from '../naming/pathBuilder'
import { writeMinimalJpeg } from '../testUtils/mediaFixtures'

describe('M1 scan pipeline (fileWalker -> exiftool -> normalize -> sequence -> name/path)', () => {
  let dir: string
  let tagWriter: ExifTool

  beforeAll(async () => {
    dir = await mkdtemp(join(tmpdir(), 'media-organizer-test-'))
    tagWriter = new ExifTool()

    // File A: has DateTimeOriginal + Make/Model
    const fileA = join(dir, 'with-exif.jpg')
    await writeMinimalJpeg(fileA)
    await tagWriter.write(
      fileA,
      {
        DateTimeOriginal: '2026:01:15 09:30:00',
        Make: 'Apple',
        Model: 'iPhone 14 Pro'
      },
      { writeArgs: ['-overwrite_original'] }
    )

    // File B: no EXIF date at all -> should fall back to mtime in M1
    const fileB = join(dir, 'no-exif.jpg')
    await writeMinimalJpeg(fileB)
    const mtimeB = new Date('2026-02-20T12:00:00')
    await utimes(fileB, mtimeB, mtimeB)

    // File C: in a subfolder, also has EXIF date, earlier than A
    await writeFile(join(dir, 'notes.txt'), 'not a media file, should be skipped')
    const subDir = join(dir, 'subfolder')
    await import('fs/promises').then((m) => m.mkdir(subDir, { recursive: true }))
    const fileC = join(subDir, 'earlier.jpg')
    await writeMinimalJpeg(fileC)
    await tagWriter.write(
      fileC,
      {
        DateTimeOriginal: '2025:12:01 08:00:00',
        Make: 'Canon',
        Model: 'EOS R5'
      },
      { writeArgs: ['-overwrite_original'] }
    )

    await tagWriter.end()
    initExiftoolPool()
  })

  afterAll(async () => {
    await shutdownExiftoolPool()
    await rm(dir, { recursive: true, force: true })
  })

  it('walks recursively, classifies media, and skips non-media files', async () => {
    const { files, skippedNonMedia } = await walkDirectory(dir, true)
    expect(files.length).toBe(3)
    expect(skippedNonMedia).toBe(1)
  })

  it('does not descend into subfolders when includeSubfolders is false', async () => {
    const { files } = await walkDirectory(dir, false)
    expect(files.map((f) => f.path.split(/[\\/]/).pop()).sort()).toEqual([
      'no-exif.jpg',
      'with-exif.jpg'
    ])
  })

  it('extracts EXIF date/camera and falls back to mtime when EXIF date is missing', async () => {
    const { files } = await walkDirectory(dir, true)

    const records = await Promise.all(
      files.map(async (file, i) => {
        const result = await readTags(file.path)
        return normalizeMetadata(file, result, String(i))
      })
    )

    const withExif = records.find((r) => r.originalPath.endsWith('with-exif.jpg'))!
    expect(withExif.model).toBe('iPhone 14 Pro')
    expect(withExif.make).toBe('Apple')
    expect(withExif.exifCapturedAt).toBeDefined()
    expect(withExif.needsResolution).toBe(false)

    const noExif = records.find((r) => r.originalPath.endsWith('no-exif.jpg'))!
    expect(noExif.exifCapturedAt).toBeUndefined()
    expect(noExif.needsResolution).toBe(true)

    const earlier = records.find((r) => r.originalPath.endsWith('earlier.jpg'))!
    expect(earlier.model).toBe('EOS R5')
  })

  it('produces correctly ordered, named, and pathed output end-to-end', async () => {
    const { files } = await walkDirectory(dir, true)
    const records = await Promise.all(
      files.map(async (file, i) => {
        const result = await readTags(file.path)
        const record = normalizeMetadata(file, result, String(i))
        if (!record.exifCapturedAt) {
          record.effectiveCapturedAt = record.mtime
          record.dateSource = 'mtimeFallback'
        } else {
          record.effectiveCapturedAt = record.exifCapturedAt
          record.dateSource = 'exif'
        }
        return record
      })
    )

    const sequenced = assignSequenceNumbers(records)
    // Chronological order: earlier.jpg (2025-12), with-exif.jpg (2026-01), no-exif.jpg (2026-02)
    expect(sequenced.map((r) => r.originalPath.split(/[\\/]/).pop())).toEqual([
      'earlier.jpg',
      'with-exif.jpg',
      'no-exif.jpg'
    ])

    const destDir = join(dir, '__dest__')
    const finalized = sequenced.map((record) => {
      const fileName = buildFileName(record, sequenced.length)
      const destPath = buildDestPath(record, destDir, fileName, 'moveToFolders')
      return { fileName, destPath }
    })

    expect(finalized[0].fileName).toBe('20251201@080000-EOS R5-1.jpg')
    expect(finalized[0].destPath).toBe(join(destDir, '2025', '2025 12', finalized[0].fileName))

    expect(finalized[1].fileName).toBe('20260115@093000-iPhone 14 Pro-2.jpg')
    expect(finalized[1].destPath).toBe(join(destDir, '2026', '2026 01', finalized[1].fileName))

    expect(finalized[2].destPath).toBe(join(destDir, '2026', '2026 02', finalized[2].fileName))
  })
})
