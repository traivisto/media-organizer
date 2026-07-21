import type { ResolutionDecision } from '../../shared/types/ScanRecord'

let pendingResolve: ((decisions: ResolutionDecision[]) => void) | undefined

/**
 * Pauses the orchestrator until the renderer submits resolution decisions (or the
 * scan is cancelled, in which case an empty decision list is returned - the caller
 * is expected to check the cancellation flag immediately after awaiting this).
 */
export function waitForResolution(): Promise<ResolutionDecision[]> {
  return new Promise((resolve) => {
    pendingResolve = resolve
  })
}

export function submitResolution(decisions: ResolutionDecision[]): void {
  pendingResolve?.(decisions)
  pendingResolve = undefined
}

export function cancelResolution(): void {
  pendingResolve?.([])
  pendingResolve = undefined
}
