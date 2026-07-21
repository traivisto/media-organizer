import { ipcMain } from 'electron'
import { IpcChannels } from '../../shared/types/IpcContracts'
import type { ExecuteDoneEvent, ExecuteProgressEvent } from '../../shared/types/IpcContracts'
import { executeMoves } from '../execution/moveEngine'
import { startNewLog, appendLogEntry, getCurrentLogPath } from '../execution/operationsLog'
import { getRunState, setRecords } from '../orchestrator/runState'
import { sendToRenderer } from './rendererBridge'

let cancelled = false

export function registerExecuteChannels(): void {
  ipcMain.handle(IpcChannels.executeStart, async () => {
    cancelled = false
    const { records, organizeMode } = getRunState()
    await startNewLog()

    const results = await executeMoves(records, {
      mode: organizeMode === 'copyToFolders' ? 'copy' : 'move',
      isCancelled: () => cancelled,
      onProgress: (processed, total, currentPath) => {
        const progress: ExecuteProgressEvent = { processed, total, currentPath }
        sendToRenderer(IpcChannels.executeProgress, progress)
      },
      onResult: (result) => {
        void appendLogEntry(result)
      }
    })

    // Reflect final status back onto the in-memory records (in case the renderer
    // re-fetches the preview after execution to see what happened to each file).
    const resultById = new Map(results.map((r) => [r.id, r]))
    setRecords(
      records.map((r) => {
        const result = resultById.get(r.id)
        return result ? { ...r, finalDestPath: result.finalDestPath ?? r.finalDestPath } : r
      })
    )

    const doneEvent: ExecuteDoneEvent = {
      results,
      movedCount: results.filter((r) => r.status === 'moved').length,
      copiedCount: results.filter((r) => r.status === 'copied').length,
      failedCount: results.filter((r) => r.status === 'failed').length,
      skippedCount: results.filter((r) => r.status === 'skipped').length,
      operationsLogPath: getCurrentLogPath() ?? ''
    }
    sendToRenderer(IpcChannels.executeDone, doneEvent)
  })

  ipcMain.handle(IpcChannels.executeCancel, () => {
    cancelled = true
  })
}
