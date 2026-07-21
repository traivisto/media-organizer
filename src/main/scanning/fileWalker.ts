import { promises as fs } from 'fs'
import { extname, join } from 'path'
import { classifyExtension } from './mediaTypes'
import type { MediaKind } from '../../shared/types/ScanRecord'

export interface WalkedFile {
  path: string
  extension: string
  mediaKind: MediaKind
  size: number
  mtime: Date
}

export interface MediaTypeFilter {
  includePhotos: boolean
  includeVideos: boolean
}

export interface WalkResult {
  files: WalkedFile[]
  skippedNonMedia: number
}

/**
 * Walks sourceDir, classifying every file as photo/video/skip. When includeSubfolders
 * is false, only the top-level directory is scanned (subfolders are not descended into).
 * Files whose media kind is excluded by mediaTypeFilter are also counted as skipped,
 * alongside genuinely non-media files.
 * Symlinks are not followed to avoid cycles; unreadable subdirectories are skipped, not fatal.
 */
export async function walkDirectory(
  sourceDir: string,
  includeSubfolders: boolean,
  mediaTypeFilter: MediaTypeFilter = { includePhotos: true, includeVideos: true }
): Promise<WalkResult> {
  const files: WalkedFile[] = []
  let skippedNonMedia = 0

  async function walk(dir: string): Promise<void> {
    let entries: import('fs').Dirent[]
    try {
      entries = await fs.readdir(dir, { withFileTypes: true })
    } catch {
      return
    }

    for (const entry of entries) {
      const fullPath = join(dir, entry.name)
      if (entry.isDirectory()) {
        if (includeSubfolders) await walk(fullPath)
        continue
      }
      if (!entry.isFile()) continue

      const ext = extname(entry.name)
      const mediaKind = classifyExtension(ext)
      if (!mediaKind) {
        skippedNonMedia += 1
        continue
      }
      if (mediaKind === 'photo' && !mediaTypeFilter.includePhotos) {
        skippedNonMedia += 1
        continue
      }
      if (mediaKind === 'video' && !mediaTypeFilter.includeVideos) {
        skippedNonMedia += 1
        continue
      }

      try {
        const stat = await fs.stat(fullPath)
        files.push({ path: fullPath, extension: ext, mediaKind, size: stat.size, mtime: stat.mtime })
      } catch {
        skippedNonMedia += 1
      }
    }
  }

  await walk(sourceDir)
  return { files, skippedNonMedia }
}
