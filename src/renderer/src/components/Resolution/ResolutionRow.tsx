import { useAppStore } from '../../state/appStore'
import { formatSystemDate } from '../../utils/formatDate'
import type { ScanRecord } from '../../../../shared/types/ScanRecord'
import type { RowDecision } from './MissingDateModal'

interface Props {
  record: ScanRecord
  decision: RowDecision
  selected: boolean
  showDestinationControl: boolean
  onToggleSelected: (checked: boolean) => void
  onChange: (patch: Partial<RowDecision>) => void
}

export function ResolutionRow({
  record,
  decision,
  selected,
  showDestinationControl,
  onToggleSelected,
  onChange
}: Props): JSX.Element {
  const windowsDatePattern = useAppStore((s) => s.windowsDatePattern)
  const excluded = !!decision.exclude
  return (
    <tr style={excluded ? { opacity: 0.5 } : undefined}>
      <td>
        <input type="checkbox" checked={selected} onChange={(e) => onToggleSelected(e.target.checked)} />
      </td>
      <td title={record.originalPath} style={{ maxWidth: 260, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
        {record.originalPath}
        {record.metadataStatus === 'unreadable' && (
          <span style={{ marginLeft: 6, fontSize: 11, color: '#c33', border: '1px solid #c33', borderRadius: 4, padding: '0 4px' }}>
            unreadable metadata
          </span>
        )}
      </td>
      <td>
        <select
          disabled={excluded}
          value={decision.dateSource}
          onChange={(e) => onChange({ dateSource: e.target.value as RowDecision['dateSource'] })}
        >
          <option value="mtimeFallback">
            Use file date ({formatSystemDate(new Date(record.mtime), windowsDatePattern)})
          </option>
          <option value="manual">Enter manually</option>
        </select>
        {decision.dateSource === 'manual' && (
          <input
            type="datetime-local"
            disabled={excluded}
            value={decision.manualCapturedAt ?? ''}
            onChange={(e) => onChange({ manualCapturedAt: e.target.value })}
            style={{ marginLeft: 6 }}
          />
        )}
      </td>
      {showDestinationControl && (
        <td>
          <label>
            <input
              type="checkbox"
              disabled={excluded}
              checked={decision.destinationOverride === 'unsorted'}
              onChange={(e) => onChange({ destinationOverride: e.target.checked ? 'unsorted' : undefined })}
            />{' '}
            Unsorted
          </label>
        </td>
      )}
      <td>
        <input
          type="checkbox"
          checked={excluded}
          onChange={(e) => onChange({ exclude: e.target.checked })}
          title="Leave this file untouched - don't rename or move it"
        />
      </td>
    </tr>
  )
}
