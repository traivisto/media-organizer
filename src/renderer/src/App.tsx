import { useAppStore } from './state/appStore'
import { PickDirsView } from './components/DirectoryPicker/PickDirsView'
import { ICloudSyncView } from './components/ICloud/ICloudSyncView'
import { ScanProgressView } from './components/Scanning/ScanProgressView'
import { MissingDateModal } from './components/Resolution/MissingDateModal'
import { TimeCorrectionView } from './components/FixTimeOrder/TimeCorrectionView'
import { PreviewSummaryBar } from './components/Preview/PreviewSummaryBar'
import { PreviewTable } from './components/Preview/PreviewTable'
import { ExecuteProgressView } from './components/Execution/ExecuteProgressView'
import { ResultsSummary } from './components/Results/ResultsSummary'
import { ArchiveUpdateView } from './components/ArchiveUpdate/ArchiveUpdateView'

function App(): JSX.Element {
  const phase = useAppStore((s) => s.phase)

  switch (phase) {
    case 'pickDirs':
      return (
        <div className="narrow-page">
          <PickDirsView />
        </div>
      )
    case 'icloudSync':
      return (
        <div className="narrow-page">
          <ICloudSyncView />
        </div>
      )
    case 'scanning':
      return (
        <div className="narrow-page">
          <ScanProgressView />
        </div>
      )
    case 'resolution':
      return <MissingDateModal />
    case 'timeCorrection':
      return <TimeCorrectionView />
    case 'preview':
      return (
        <div>
          <h1>Preview</h1>
          <PreviewSummaryBar />
          <PreviewTable />
        </div>
      )
    case 'executing':
      return (
        <div className="narrow-page">
          <ExecuteProgressView />
        </div>
      )
    case 'results':
      return (
        <div className="narrow-page">
          <ResultsSummary />
        </div>
      )
    case 'archiveUpdate':
      return (
        <div className="narrow-page">
          <ArchiveUpdateView />
        </div>
      )
    default:
      return <p>Not implemented yet: {phase}</p>
  }
}

export default App
