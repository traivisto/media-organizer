import PQueue from 'p-queue'

// Nominatim's usage policy caps public API requests at ~1/sec.
const queue = new PQueue({ concurrency: 1, interval: 1100, intervalCap: 1 })

export function enqueueGeocodeRequest<T>(task: () => Promise<T>): Promise<T> {
  return queue.add(task) as Promise<T>
}
