import { useEffect, useRef } from 'react'

export function usePolling(callback: () => void | Promise<void>, intervalMs: number, enabled = true) {
  const saved = useRef(callback)

  useEffect(() => {
    saved.current = callback
  }, [callback])

  useEffect(() => {
    if (!enabled) return

    let cancelled = false

    const tick = async () => {
      if (cancelled) return
      await saved.current()
    }

    void tick()
    const id = window.setInterval(() => {
      void tick()
    }, intervalMs)

    return () => {
      cancelled = true
      window.clearInterval(id)
    }
  }, [intervalMs, enabled])
}
