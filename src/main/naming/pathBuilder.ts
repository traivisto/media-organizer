import { dirname, join } from 'path'
import type { ScanRecord } from '../../shared/types/ScanRecord'
import type { OrganizeMode } from '../../shared/types/IpcContracts'

/**
 * Computes the destination path for a record.
 * - renameInPlace: the file stays in its original folder, only the name changes.
 * - moveToFolders / copyToFolders: dest/YYYY/YYYY MM (month folder self-labeled with the
 *   year so it reads sensibly on its own, e.g. "2007 05"), or dest/Unsorted when the
 *   record was explicitly routed there during missing-date resolution. The two modes
 *   share the same path logic - they only differ in whether execution deletes the source.
 */
export function buildDestPath(
  record: ScanRecord,
  destDir: string | undefined,
  fileName: string,
  organizeMode: OrganizeMode
): string {
  if (organizeMode === 'renameInPlace') {
    return join(dirname(record.originalPath), fileName)
  }

  if (record.destinationOverride === 'unsorted') {
    return join(destDir!, 'Unsorted', fileName)
  }

  const iso = record.effectiveCapturedAt ?? record.exifCapturedAt ?? record.mtime
  const date = new Date(iso)
  const year = String(date.getFullYear())
  const month = String(date.getMonth() + 1).padStart(2, '0')
  return join(destDir!, year, `${year} ${month}`, fileName)
}
