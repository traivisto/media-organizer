import { useMemo, useState } from 'react'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'
import { BulkActionBar } from './BulkActionBar'
import { ResolutionRow } from './ResolutionRow'
import type { DateSource } from '../../../../shared/types/DateSource'
import type { ResolutionDecision } from '../../../../shared/types/ScanRecord'

export interface RowDecision {
  exclude?: boolean
  dateSource: Extract<DateSource, 'mtimeFallback' | 'manual'>
  manualCapturedAt?: string
  destinationOverride?: 'unsorted'
}

export function MissingDateModal(): JSX.Element {
  const { needsResolutionRecords, organizeMode, setPhase } = useAppStore()
  const showDestinationControl = organizeMode === 'moveToFolders'

  const [decisions, setDecisions] = useState<Record<string, RowDecision>>(() =>
    Object.fromEntries(needsResolutionRecords.map((r) => [r.id, { dateSource: 'mtimeFallback' as const }]))
  )
  const [selectedIds, setSelectedIds] = useState<Set<string>>(
    () => new Set(needsResolutionRecords.map((r) => r.id))
  )
  const [error, setError] = useState<string>()

  const allSelected = selectedIds.size === needsResolutionRecords.length

  function updateDecision(id: string, patch: Partial<RowDecision>): void {
    setDecisions((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }))
  }

  function toggleSelected(id: string, checked: boolean): void {
    setSelectedIds((prev) => {
      const next = new Set(prev)
      if (checked) next.add(id)
      else next.delete(id)
      return next
    })
  }

  function toggleSelectAll(checked: boolean): void {
    setSelectedIds(checked ? new Set(needsResolutionRecords.map((r) => r.id)) : new Set())
  }

  function applyDateSourceToSelected(
    dateSource: Extract<DateSource, 'mtimeFallback' | 'manual'>,
    manualCapturedAt?: string
  ): void {
    setDecisions((prev) => {
      const next = { ...prev }
      for (const id of selectedIds) next[id] = { ...next[id], dateSource, manualCapturedAt }
      return next
    })
  }

  function applyDestinationToSelected(destinationOverride: 'unsorted' | undefined): void {
    setDecisions((prev) => {
      const next = { ...prev }
      for (const id of selectedIds) next[id] = { ...next[id], destinationOverride }
      return next
    })
  }

  function applyExcludeToSelected(exclude: boolean): void {
    setDecisions((prev) => {
      const next = { ...prev }
      for (const id of selectedIds) next[id] = { ...next[id], exclude }
      return next
    })
  }

  const invalidCount = useMemo(
    () =>
      Object.values(decisions).filter(
        (d) => !d.exclude && d.dateSource === 'manual' && !d.manualCapturedAt
      ).length,
    [decisions]
  )

  async function submit(): Promise<void> {
    if (invalidCount > 0) {
      setError(`${invalidCount} file(s) set to "Enter manually" still need a date.`)
      return
    }
    setError(undefined)

    const payload: ResolutionDecision[] = needsResolutionRecords.map((r) => ({
      id: r.id,
      ...decisions[r.id]
    }))

    setPhase('scanning')
    await api().resolutionSubmit({ decisions: payload })
  }

  const excludedCount = Object.values(decisions).filter((d) => d.exclude).length

  return (
    <div>
      <h1>Some files need a date</h1>
      <p>
        {needsResolutionRecords.length} file(s) don&apos;t have a usable date in their metadata. Choose how
        to date and sort each one, or exclude a file to leave it untouched (select several and apply a
        choice in bulk if useful).
        {excludedCount > 0 && ` ${excludedCount} file(s) will be excluded.`}
      </p>

      <BulkActionBar
        allSelected={allSelected}
        selectedCount={selectedIds.size}
        showDestinationControl={showDestinationControl}
        onToggleSelectAll={toggleSelectAll}
        onApplyDateSource={applyDateSourceToSelected}
        onApplyDestination={applyDestinationToSelected}
        onApplyExclude={applyExcludeToSelected}
      />

      {error && <p style={{ color: '#c33' }}>{error}</p>}

      <table>
        <thead>
          <tr>
            <th></th>
            <th>File</th>
            <th>Date</th>
            {showDestinationControl && <th>Destination</th>}
            <th>Exclude</th>
          </tr>
        </thead>
        <tbody>
          {needsResolutionRecords.map((record) => (
            <ResolutionRow
              key={record.id}
              record={record}
              decision={decisions[record.id]}
              selected={selectedIds.has(record.id)}
              showDestinationControl={showDestinationControl}
              onToggleSelected={(checked) => toggleSelected(record.id, checked)}
              onChange={(patch) => updateDecision(record.id, patch)}
            />
          ))}
        </tbody>
      </table>

      <div style={{ marginTop: 16 }}>
        <button onClick={submit}>Continue</button>
      </div>
    </div>
  )
}
