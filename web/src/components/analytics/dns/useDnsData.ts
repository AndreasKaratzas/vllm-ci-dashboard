import { onBeforeUnmount, onMounted, ref, shallowRef } from 'vue'
import { dataUrl } from '@/data/fetch'
import { createDnsLoader, DNS_AUTO_REFRESH_MS } from '@/domain/dns'
import type { Rec } from '@/domain/format'

// Classic fetchJSON semantics (three bounded attempts, no-store), kept local so a
// DNS refresh invalidates only the two DNS sources and not the page-wide cache.
async function fetchDns(source: string): Promise<unknown> {
  let lastError: unknown = null
  for (let attempt = 0; attempt < 3; attempt += 1) {
    try {
      const url = dataUrl(source)
      const response = await fetch(`${url}${url.includes('?') ? '&' : '?'}_health=${Date.now()}-${attempt}`, { cache: 'no-store' })
      if (!response.ok) throw new Error(`${source} returned HTTP ${response.status}`)
      return await response.json()
    } catch (error) {
      lastError = error
      if (attempt < 2) await new Promise((resolve) => setTimeout(resolve, attempt === 0 ? 250 : 1000))
    }
  }
  throw lastError ?? new Error(`${source} could not be fetched`)
}

const payload = shallowRef<Rec | null>(null)
const error = ref<string | null>(null)
const loading = ref(false)
// A slower source that carries a newer publication upgrades the visible view.
const loader = createDnsLoader(fetchDns, (newer) => {
  payload.value = newer
})

async function load() {
  loading.value = true
  try {
    payload.value = await loader.load()
    error.value = null
  } catch (e) {
    error.value = e instanceof Error ? e.message : String(e)
  } finally {
    loading.value = false
  }
}

async function refresh() {
  loader.invalidate()
  await load()
}

/** DNS observations with the classic live/Pages arbitration and a 5-minute refresh while visible. */
export function useDnsData() {
  let timer: ReturnType<typeof setInterval> | null = null
  const onVisibility = () => {
    if (document.visibilityState === 'visible' && loader.refreshDue()) void refresh()
  }
  onMounted(() => {
    void load()
    timer = setInterval(() => {
      if (document.visibilityState !== 'hidden') void refresh()
    }, DNS_AUTO_REFRESH_MS)
    document.addEventListener('visibilitychange', onVisibility)
  })
  onBeforeUnmount(() => {
    if (timer) clearInterval(timer)
    document.removeEventListener('visibilitychange', onVisibility)
  })
  return { payload, error, loading, retry: refresh }
}
