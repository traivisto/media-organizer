import { registerDialogChannels } from './dialogChannels'
import { registerScanChannels } from './scanChannels'
import { registerResolutionChannels } from './resolutionChannels'
import { registerTimeCorrectionChannels } from './timeCorrectionChannels'
import { registerPreviewChannels } from './previewChannels'
import { registerExecuteChannels } from './executeChannels'
import { registerIcloudChannels } from './icloudChannels'
import { registerArchiveChannels } from './archiveChannels'

export function registerIpcHandlers(): void {
  registerDialogChannels()
  registerScanChannels()
  registerResolutionChannels()
  registerTimeCorrectionChannels()
  registerPreviewChannels()
  registerExecuteChannels()
  registerIcloudChannels()
  registerArchiveChannels()
}
