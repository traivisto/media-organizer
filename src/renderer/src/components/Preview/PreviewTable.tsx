import { useEffect, useRef, useState } from 'react'
import { FixedSizeList } from 'react-window'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'
import { formatSystemDateTime } from '../../utils/formatDate'
import type { ScanRecord } from '../../../../shared/types/ScanRecord'

const PAGE_SIZE = 500
const ROW_HEIGHT = 28
const MIN_COLUMN_WIDTH = 70
const COLUMN_LABELS = ['Original', 'New name', 'Date', 'Camera', 'Location']
// Relative starting proportions (Original/New name get more room than Date/Camera/Location).
const COLUMN_WEIGHTS = [2, 2, 1.5, 1, 1]

const ELLIPSIS_STYLE = {
  minWidth: 0,
  overflow: 'hidden',
  textOverflow: 'ellipsis',
  whiteSpace: 'nowrap'
} as const

function formatDate(record: ScanRecord, datePattern: string | undefined, timePattern: string | undefined): string {
  const iso = record.effectiveCapturedAt ?? record.exifCapturedAt ?? record.mtime
  return `${formatSystemDateTime(new Date(iso), datePattern, timePattern)} (${record.dateSource ?? 'exif'})`
}

function getCellValue(r: ScanRecord, colIndex: number, datePattern?: string, timePattern?: string): string {
  switch (colIndex) {
    case 0:
      return r.originalPath
    case 1:
      return r.finalFileName ?? ''
    case 2:
      return formatDate(r, datePattern, timePattern)
    case 3:
      return r.model ?? '-'
    default:
      return r.location ?? '-'
  }
}

export function PreviewTable(): JSX.Element {
  const windowsDatePattern = useAppStore((s) => s.windowsDatePattern)
  const windowsTimePattern = useAppStore((s) => s.windowsTimePattern)
  const [records, setRecords] = useState<ScanRecord[]>([])
  const [loading, setLoading] = useState(true)

  const containerRef = useRef<HTMLDivElement>(null)
  const [colWidths, setColWidths] = useState<number[]>(() => COLUMN_WEIGHTS.map((w) => w * 120))
  const dragRef = useRef<{ index: number; startX: number; startWidth: number } | null>(null)

  useEffect(() => {
    let cancelled = false

    async function loadAll(): Promise<void> {
      const all: ScanRecord[] = []
      let offset = 0
      // eslint-disable-next-line no-constant-condition
      while (true) {
        const res = await api().previewGet({ offset, limit: PAGE_SIZE })
        all.push(...res.records)
        offset += res.records.length
        if (cancelled || res.records.length === 0 || offset >= res.totalCount) break
      }
      if (!cancelled) {
        setRecords(all)
        setLoading(false)
      }
    }

    void loadAll()
    return () => {
      cancelled = true
    }
  }, [])

  // Size columns to fill the available width once, on first mount.
  useEffect(() => {
    const container = containerRef.current
    if (!container) return
    const available = container.clientWidth - 16 // vertical scrollbar allowance
    const totalWeight = COLUMN_WEIGHTS.reduce((a, b) => a + b, 0)
    setColWidths(
      COLUMN_WEIGHTS.map((w) => Math.max(MIN_COLUMN_WIDTH, Math.floor((w / totalWeight) * available)))
    )
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function startDrag(index: number, e: React.MouseEvent): void {
    dragRef.current = { index, startX: e.clientX, startWidth: colWidths[index] }

    function onMouseMove(moveEvent: MouseEvent): void {
      const drag = dragRef.current
      if (!drag) return
      const delta = moveEvent.clientX - drag.startX
      setColWidths((prev) => {
        const next = [...prev]
        next[drag.index] = Math.max(MIN_COLUMN_WIDTH, drag.startWidth + delta)
        return next
      })
    }

    function onMouseUp(): void {
      dragRef.current = null
      window.removeEventListener('mousemove', onMouseMove)
      window.removeEventListener('mouseup', onMouseUp)
    }

    window.addEventListener('mousemove', onMouseMove)
    window.addEventListener('mouseup', onMouseUp)
  }

  if (loading) return <p>Loading preview...</p>

  const gridTemplateColumns = colWidths.map((w) => `${w}px`).join(' ') + ' minmax(0, 1fr)'

  return (
    <div ref={containerRef} style={{ minWidth: 0 }}>
      <div style={{ display: 'grid', gridTemplateColumns, fontWeight: 600, padding: '4px 8px' }}>
        {COLUMN_LABELS.map((label, i) => (
          <div key={label} style={{ position: 'relative', ...ELLIPSIS_STYLE }}>
            {label}
            <div
              onMouseDown={(e) => startDrag(i, e)}
              style={{
                position: 'absolute',
                right: 0,
                top: 0,
                bottom: 0,
                width: 6,
                cursor: 'col-resize',
                userSelect: 'none'
              }}
            />
          </div>
        ))}
        <div />
      </div>
      <FixedSizeList height={480} width="100%" itemCount={records.length} itemSize={ROW_HEIGHT}>
        {({ index, style }) => {
          const r = records[index]
          return (
            <div
              style={{
                ...style,
                display: 'grid',
                gridTemplateColumns,
                padding: '0 8px',
                alignItems: 'center',
                fontSize: 12,
                borderBottom: '1px solid #8882'
              }}
              title={r.originalPath}
            >
              {COLUMN_LABELS.map((label, i) => (
                <div key={label} style={ELLIPSIS_STYLE}>
                  {getCellValue(r, i, windowsDatePattern, windowsTimePattern)}
                </div>
              ))}
              <div />
            </div>
          )
        }}
      </FixedSizeList>
    </div>
  )
}
