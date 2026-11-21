import { useState } from 'react'
import { api } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'
import { formatSystemDateTime } from '../../utils/formatDate'
import type { CameraGroup, TimeCorrectionRule } from '../../../../shared/types/IpcContracts'

interface OffsetInput {
  sign: 1 | -1
  hours: number
  minutes: number
}

const ZERO_OFFSET: OffsetInput = { sign: 1, hours: 0, minutes: 0 }

function groupKey(g: Pick<CameraGroup, 'make' | 'model'>): string {
  return `${g.make ?? ''}|${g.model ?? ''}`
}

function groupLabel(g: CameraGroup): string {
  const name = [g.make, g.model].filter(Boolean).join(' ')
  return name || 'No camera info (e.g. WhatsApp images)'
}

export function TimeCorrectionView(): JSX.Element {
  const { cameraGroups, windowsDatePattern, windowsTimePattern, setPhase } = useAppStore()

  const [offsets, setOffsets] = useState<Record<string, OffsetInput>>(() =>
    Object.fromEntries(cameraGroups.map((g) => [groupKey(g), ZERO_OFFSET]))
  )

  function updateOffset(key: string, patch: Partial<OffsetInput>): void {
    setOffsets((prev) => ({ ...prev, [key]: { ...(prev[key] ?? ZERO_OFFSET), ...patch } }))
  }

  function formatRange(g: CameraGroup): string {
    const from = formatSystemDateTime(new Date(g.minCapturedAt), windowsDatePattern, windowsTimePattern)
    const to = formatSystemDateTime(new Date(g.maxCapturedAt), windowsDatePattern, windowsTimePattern)
    return from === to ? from : `${from} – ${to}`
  }

  async function submit(): Promise<void> {
    const rules: TimeCorrectionRule[] = cameraGroups.map((g) => {
      const o = offsets[groupKey(g)] ?? ZERO_OFFSET
      return { make: g.make, model: g.model, offsetMinutes: o.sign * (o.hours * 60 + o.minutes) }
    })

    setPhase('scanning')
    await api().timeCorrectionSubmit({ rules })
  }

  return (
    <div>
      <h1>Correct camera times</h1>
      <p>
        These are the cameras found in this folder. If one was set to the wrong timezone or its
        clock had drifted, enter how far off it was - the offset is applied only to sorting and
        renaming, the original files are left untouched. Leave a camera at 0:00 to make no change.
      </p>

      <table>
        <thead>
          <tr>
            <th>Camera</th>
            <th>Files</th>
            <th>Date range</th>
            <th>Offset</th>
          </tr>
        </thead>
        <tbody>
          {cameraGroups.map((g) => {
            const key = groupKey(g)
            const offset = offsets[key] ?? ZERO_OFFSET
            return (
              <tr key={key}>
                <td>{groupLabel(g)}</td>
                <td>{g.count}</td>
                <td style={{ fontSize: 12 }}>{formatRange(g)}</td>
                <td>
                  <select
                    value={offset.sign}
                    onChange={(e) => updateOffset(key, { sign: Number(e.target.value) as 1 | -1 })}
                  >
                    <option value={1}>+</option>
                    <option value={-1}>-</option>
                  </select>{' '}
                  <input
                    type="number"
                    min={0}
                    max={23}
                    value={offset.hours}
                    onChange={(e) => updateOffset(key, { hours: Number(e.target.value) })}
                    style={{ width: 48 }}
                  />
                  h{' '}
                  <input
                    type="number"
                    min={0}
                    max={59}
                    value={offset.minutes}
                    onChange={(e) => updateOffset(key, { minutes: Number(e.target.value) })}
                    style={{ width: 48 }}
                  />
                  m
                </td>
              </tr>
            )
          })}
        </tbody>
      </table>

      <div style={{ marginTop: 16 }}>
        <button onClick={submit}>Continue</button>
      </div>
    </div>
  )
}
