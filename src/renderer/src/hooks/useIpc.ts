import { useEffect } from 'react'

export function api(): Window['api'] {
  return window.api
}

export function useIpcEvent<T>(
  subscribe: (cb: (payload: T) => void) => () => void,
  callback: (payload: T) => void
): void {
  useEffect(() => {
    const unsubscribe = subscribe(callback)
    return unsubscribe
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
