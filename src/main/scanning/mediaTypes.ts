export const PHOTO_EXTENSIONS = new Set([
  '.jpg',
  '.jpeg',
  '.png',
  '.heic',
  '.heif',
  '.tif',
  '.tiff',
  '.webp',
  '.bmp',
  '.gif',
  '.raw',
  '.cr2',
  '.cr3',
  '.nef',
  '.arw',
  '.dng',
  '.orf',
  '.rw2'
])

export const VIDEO_EXTENSIONS = new Set([
  '.mp4',
  '.mov',
  '.m4v',
  '.avi',
  '.mkv',
  '.3gp',
  '.mts',
  '.m2ts',
  '.wmv',
  '.webm'
])

export function classifyExtension(ext: string): 'photo' | 'video' | undefined {
  const lower = ext.toLowerCase()
  if (PHOTO_EXTENSIONS.has(lower)) return 'photo'
  if (VIDEO_EXTENSIONS.has(lower)) return 'video'
  return undefined
}
