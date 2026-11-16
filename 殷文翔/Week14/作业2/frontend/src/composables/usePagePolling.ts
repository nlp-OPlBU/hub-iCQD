import { onBeforeUnmount, onMounted } from 'vue'

const configuredInterval = Number(import.meta.env.VITE_POLL_INTERVAL_MS)
const interval = Number.isFinite(configuredInterval) && configuredInterval >= 1000 ? configuredInterval : 3000

// Schedule only after the previous request settles; never rearm after navigation.
export function usePagePolling(update: (signal: AbortSignal) => Promise<void>) {
  const controller = new AbortController()
  let timer: ReturnType<typeof setTimeout> | undefined
  async function tick() {
    try { await update(controller.signal) }
    finally {
      if (!controller.signal.aborted) timer = setTimeout(() => { void tick() }, interval)
    }
  }
  onMounted(() => { void tick() })
  onBeforeUnmount(() => {
    controller.abort()
    if (timer) clearTimeout(timer)
  })
  return controller.signal
}
