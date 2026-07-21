import { registerDialogChannels } from './dialogChannels'
import { registerScanChannels } from './scanChannels'
import { registerResolutionChannels } from './resolutionChannels'
import { registerPreviewChannels } from './previewChannels'
import { registerExecuteChannels } from './executeChannels'

export function registerIpcHandlers(): void {
  registerDialogChannels()
  registerScanChannels()
  registerResolutionChannels()
  registerPreviewChannels()
  registerExecuteChannels()
}
