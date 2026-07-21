import { api, useIpcEvent } from '../../hooks/useIpc'
import { useAppStore } from '../../state/appStore'

export function ExecuteProgressView(): JSX.Element {
  const { executeProgress, setExecuteProgress, setExecuteResult, setPhase } = useAppStore()

  useIpcEvent(api().onExecuteProgress, setExecuteProgress)
  useIpcEvent(api().onExecuteDone, (e) => {
    setExecuteResult(e)
    setPhase('results')
  })

  const processed = executeProgress?.processed ?? 0
  const total = executeProgress?.total ?? 0
  const pct = total > 0 ? Math.round((processed / total) * 100) : 0

  return (
    <div>
      <h1>Moving files...</h1>
      <p>
        {processed} / {total} files ({pct}%)
      </p>
      {executeProgress?.currentPath && (
        <p style={{ fontSize: 12, opacity: 0.7 }}>{executeProgress.currentPath}</p>
      )}
      <button onClick={() => api().executeCancel()}>Cancel</button>
    </div>
  )
}
