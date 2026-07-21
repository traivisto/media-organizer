import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function PreviewSummaryBar(): JSX.Element {
  const {
    totalCount,
    flaggedCount,
    errorCount,
    excludedByDateRange,
    excludedManually,
    destDir,
    organizeMode,
    setPhase
  } = useAppStore()
  const isRenameInPlace = organizeMode === 'renameInPlace'
  const isCopy = organizeMode === 'copyToFolders'

  async function confirmAndMove(): Promise<void> {
    const message = isRenameInPlace
      ? `This will RENAME ${totalCount} file(s) in place, in their current folder(s). Files will not be moved anywhere. Continue?`
      : isCopy
        ? `This will COPY ${totalCount} file(s) into\n${destDir}\n\nOriginals will remain untouched in the source folder. Continue?`
        : `This will MOVE ${totalCount} file(s) out of their source folder and into\n${destDir}\n\nOriginals will no longer be in the source folder. Continue?`

    if (!window.confirm(message)) return

    setPhase('executing')
    await api().executeStart()
  }

  return (
    <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12 }}>
      <div>
        <strong>{totalCount}</strong> files ready &middot; <strong>{flaggedCount}</strong> flagged &middot;{' '}
        <strong>{errorCount}</strong> errors
        {excludedByDateRange > 0 && (
          <>
            {' '}
            &middot; <strong>{excludedByDateRange}</strong> outside date range
          </>
        )}
        {excludedManually > 0 && (
          <>
            {' '}
            &middot; <strong>{excludedManually}</strong> excluded
          </>
        )}
      </div>
      <div style={{ display: 'flex', gap: 8 }}>
        <button onClick={() => setPhase('pickDirs')}>Back</button>
        <button disabled={totalCount === 0} onClick={confirmAndMove}>
          {isRenameInPlace ? 'Confirm & Rename' : isCopy ? 'Confirm & Copy' : 'Confirm & Move'}
        </button>
      </div>
    </div>
  )
}
