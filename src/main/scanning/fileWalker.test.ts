import { afterEach, describe, expect, it } from 'vitest'
import { mkdtemp, rm, writeFile } from 'fs/promises'
import { tmpdir } from 'os'
import { join } from 'path'
import { walkDirectory } from './fileWalker'

describe('walkDirectory media type filter', () => {
  let dir: string

  afterEach(async () => {
    if (dir) await rm(dir, { recursive: true, force: true })
  })

  async function makeTestDir(): Promise<string> {
    dir = await mkdtemp(join(tmpdir(), 'media-organizer-walker-'))
    await writeFile(join(dir, 'photo.jpg'), 'x')
    await writeFile(join(dir, 'video.mp4'), 'x')
    await writeFile(join(dir, 'notes.txt'), 'x')
    return dir
  }

  it('includes both photos and videos by default', async () => {
    const d = await makeTestDir()
    const { files, skippedNonMedia } = await walkDirectory(d, false)
    expect(files.map((f) => f.mediaKind).sort()).toEqual(['photo', 'video'])
    expect(skippedNonMedia).toBe(1) // notes.txt
  })

  it('excludes videos when includeVideos is false', async () => {
    const d = await makeTestDir()
    const { files, skippedNonMedia } = await walkDirectory(d, false, {
      includePhotos: true,
      includeVideos: false
    })
    expect(files.map((f) => f.mediaKind)).toEqual(['photo'])
    expect(skippedNonMedia).toBe(2) // notes.txt + video.mp4
  })

  it('excludes photos when includePhotos is false', async () => {
    const d = await makeTestDir()
    const { files, skippedNonMedia } = await walkDirectory(d, false, {
      includePhotos: false,
      includeVideos: true
    })
    expect(files.map((f) => f.mediaKind)).toEqual(['video'])
    expect(skippedNonMedia).toBe(2) // notes.txt + photo.jpg
  })
})
