import type { TimeCorrectionRule } from '../../shared/types/IpcContracts'

let pendingResolve: ((rules: TimeCorrectionRule[]) => void) | undefined

/**
 * Pauses the orchestrator (fixTimeOrder runs only) until the renderer submits the
 * per-camera offset rules, mirroring resolutionGate's cancel-returns-empty contract.
 */
export function waitForTimeCorrection(): Promise<TimeCorrectionRule[]> {
  return new Promise((resolve) => {
    pendingResolve = resolve
  })
}

export function submitTimeCorrection(rules: TimeCorrectionRule[]): void {
  pendingResolve?.(rules)
  pendingResolve = undefined
}

export function cancelTimeCorrection(): void {
  pendingResolve?.([])
  pendingResolve = undefined
}
