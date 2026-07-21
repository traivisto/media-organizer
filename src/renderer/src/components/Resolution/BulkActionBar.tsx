import { useState } from 'react'
import type { DateSource } from '../../../../shared/types/DateSource'

interface Props {
  allSelected: boolean
  selectedCount: number
  showDestinationControl: boolean
  onToggleSelectAll: (checked: boolean) => void
  onApplyDateSource: (dateSource: Extract<DateSource, 'mtimeFallback' | 'manual'>, manualCapturedAt?: string) => void
  onApplyDestination: (destinationOverride: 'unsorted' | undefined) => void
  onApplyExclude: (exclude: boolean) => void
}

export function BulkActionBar({
  allSelected,
  selectedCount,
  showDestinationControl,
  onToggleSelectAll,
  onApplyDateSource,
  onApplyDestination,
  onApplyExclude
}: Props): JSX.Element {
  const [bulkDateSource, setBulkDateSource] = useState<Extract<DateSource, 'mtimeFallback' | 'manual'>>(
    'mtimeFallback'
  )
  const [bulkManualDate, setBulkManualDate] = useState('')

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 12, padding: '8px 0', flexWrap: 'wrap' }}>
      <label>
        <input type="checkbox" checked={allSelected} onChange={(e) => onToggleSelectAll(e.target.checked)} />{' '}
        Select all
      </label>
      <span style={{ fontSize: 12, opacity: 0.7 }}>{selectedCount} selected</span>

      <select value={bulkDateSource} onChange={(e) => setBulkDateSource(e.target.value as typeof bulkDateSource)}>
        <option value="mtimeFallback">Use file date</option>
        <option value="manual">Enter manually</option>
      </select>
      {bulkDateSource === 'manual' && (
        <input
          type="datetime-local"
          value={bulkManualDate}
          onChange={(e) => setBulkManualDate(e.target.value)}
        />
      )}
      <button
        disabled={selectedCount === 0}
        onClick={() => onApplyDateSource(bulkDateSource, bulkDateSource === 'manual' ? bulkManualDate : undefined)}
      >
        Apply date to selected
      </button>

      {showDestinationControl && (
        <>
          <button disabled={selectedCount === 0} onClick={() => onApplyDestination('unsorted')}>
            Send selected to Unsorted
          </button>
          <button disabled={selectedCount === 0} onClick={() => onApplyDestination(undefined)}>
            Keep selected in dated folders
          </button>
        </>
      )}

      <button disabled={selectedCount === 0} onClick={() => onApplyExclude(true)}>
        Exclude selected
      </button>
      <button disabled={selectedCount === 0} onClick={() => onApplyExclude(false)}>
        Include selected
      </button>
    </div>
  )
}
