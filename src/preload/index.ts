import { contextBridge, ipcRenderer } from 'electron'
import { IpcChannels } from '../shared/types/IpcContracts'
import type {
  AppConfig,
  ArchiveUpdateStatusEvent,
  ExecuteDoneEvent,
  ExecuteProgressEvent,
  IcloudSyncStartRequest,
  IcloudSyncStatusEvent,
  PreviewGetRequest,
  PreviewGetResponse,
  ResolutionSubmitRequest,
  ScanNeedsResolutionEvent,
  ScanNeedsTimeCorrectionEvent,
  ScanProgressEvent,
  ScanReadyForPreviewEvent,
  ScanStartRequest,
  TimeCorrectionSubmitRequest
} from '../shared/types/IpcContracts'

function on<T>(channel: string, callback: (payload: T) => void): () => void {
  const listener = (_event: Electron.IpcRendererEvent, payload: T): void => callback(payload)
  ipcRenderer.on(channel, listener)
  return () => ipcRenderer.removeListener(channel, listener)
}

const api = {
  pickSourceDir: (): Promise<string | undefined> => ipcRenderer.invoke(IpcChannels.pickSourceDir),
  pickDestDir: (): Promise<string | undefined> => ipcRenderer.invoke(IpcChannels.pickDestDir),
  getConfig: (): Promise<AppConfig> => ipcRenderer.invoke(IpcChannels.getConfig),

  scanStart: (req: ScanStartRequest): Promise<void> => ipcRenderer.invoke(IpcChannels.scanStart, req),
  scanCancel: (): Promise<void> => ipcRenderer.invoke(IpcChannels.scanCancel),
  onScanCancelled: (cb: (e: undefined) => void) => on(IpcChannels.scanCancelled, cb),
  onScanProgress: (cb: (e: ScanProgressEvent) => void) => on(IpcChannels.scanProgress, cb),
  onScanNeedsResolution: (cb: (e: ScanNeedsResolutionEvent) => void) =>
    on(IpcChannels.scanNeedsResolution, cb),
  onScanNeedsTimeCorrection: (cb: (e: ScanNeedsTimeCorrectionEvent) => void) =>
    on(IpcChannels.scanNeedsTimeCorrection, cb),
  onScanReadyForPreview: (cb: (e: ScanReadyForPreviewEvent) => void) =>
    on(IpcChannels.scanReadyForPreview, cb),

  resolutionSubmit: (req: ResolutionSubmitRequest): Promise<void> =>
    ipcRenderer.invoke(IpcChannels.resolutionSubmit, req),

  timeCorrectionSubmit: (req: TimeCorrectionSubmitRequest): Promise<void> =>
    ipcRenderer.invoke(IpcChannels.timeCorrectionSubmit, req),

  previewGet: (req: PreviewGetRequest): Promise<PreviewGetResponse> =>
    ipcRenderer.invoke(IpcChannels.previewGet, req),

  executeStart: (): Promise<void> => ipcRenderer.invoke(IpcChannels.executeStart),
  executeCancel: (): Promise<void> => ipcRenderer.invoke(IpcChannels.executeCancel),
  onExecuteProgress: (cb: (e: ExecuteProgressEvent) => void) => on(IpcChannels.executeProgress, cb),
  onExecuteDone: (cb: (e: ExecuteDoneEvent) => void) => on(IpcChannels.executeDone, cb),

  icloudSyncStart: (req: IcloudSyncStartRequest): Promise<void> =>
    ipcRenderer.invoke(IpcChannels.icloudSyncStart, req),
  icloudSyncCancel: (): Promise<void> => ipcRenderer.invoke(IpcChannels.icloudSyncCancel),
  onIcloudSyncStatus: (cb: (e: IcloudSyncStatusEvent) => void) => on(IpcChannels.icloudSyncStatus, cb),

  setPreviewColumnWidths: (widths: number[]): Promise<void> =>
    ipcRenderer.invoke(IpcChannels.setPreviewColumnWidths, widths),

  archiveUpdateStart: (): Promise<void> => ipcRenderer.invoke(IpcChannels.archiveUpdateStart),
  archiveUpdateCancel: (): Promise<void> => ipcRenderer.invoke(IpcChannels.archiveUpdateCancel),
  onArchiveUpdateStatus: (cb: (e: ArchiveUpdateStatusEvent) => void) =>
    on(IpcChannels.archiveUpdateStatus, cb),
  /** Resolves to an error message, or undefined when the folder opened. */
  openFolder: (dir: string): Promise<string | undefined> =>
    ipcRenderer.invoke(IpcChannels.openFolder, dir)
}

export type Api = typeof api

contextBridge.exposeInMainWorld('api', api)
