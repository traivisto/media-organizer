import type { MoveResult } from '../../../../shared/types/ScanRecord'

interface Props {
  results: MoveResult[]
}

export function ResultsErrorList({ results }: Props): JSX.Element | null {
  const failed = results.filter((r) => r.status === 'failed' || r.status === 'skipped')
  if (failed.length === 0) return null

  return (
    <div style={{ marginTop: 16 }}>
      <h2>Problems ({failed.length})</h2>
      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Status</th>
            <th>Reason</th>
          </tr>
        </thead>
        <tbody>
          {failed.map((r) => (
            <tr key={r.id}>
              <td title={r.originalPath}>{r.originalPath}</td>
              <td>{r.status}</td>
              <td>{r.error}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
