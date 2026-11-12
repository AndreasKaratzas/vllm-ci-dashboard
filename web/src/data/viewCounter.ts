import settings from '@config/view_counter.json'
import { START_LOCATION, type Router } from 'vue-router'

export interface ViewCounterSettings {
  goatcounter_code: string
  site_url: string
}

type Page = Pick<Location, 'origin' | 'pathname'>

/**
 * GoatCounter origin when this page is the published site, else null so PR
 * previews, local dev, tests and browser automation never count.
 */
export function counterOrigin(page: Page, config: ViewCounterSettings = settings, automated = false): string | null {
  if (automated || !/^[a-z0-9-]+$/.test(config.goatcounter_code)) return null
  const site = new URL(config.site_url)
  if (page.origin !== site.origin || page.pathname.replace(/index\.html$/, '') !== site.pathname) return null
  return `https://${config.goatcounter_code}.goatcounter.com`
}

export const VIEW_COUNTER = typeof window === 'undefined' ? null : counterOrigin(window.location, settings, navigator.webdriver)

/** Counts the first load and every change of route path; query-only changes are filters, not views. */
export function trackViews(router: Router, origin: string, send: (url: string) => void = beacon) {
  let referrer = document.referrer
  router.afterEach((to, from, failure) => {
    if (failure || (from !== START_LOCATION && to.path === from.path)) return
    const params = new URLSearchParams({ p: to.path, t: String((to.meta.tab as { label?: string } | undefined)?.label ?? document.title) })
    if (referrer) params.set('r', referrer)
    params.set('s', `${screen.width},${screen.height},${window.devicePixelRatio || 1}`)
    params.set('rnd', Math.random().toString(36).slice(2))
    referrer = ''
    send(`${origin}/count?${params}`)
  })
}

function beacon(url: string) {
  try {
    navigator.sendBeacon(url)
  } catch {
    // Blocked by an extension or CSP; counting is best effort.
  }
}

/** Site-wide views over the last 7 days as GoatCounter formats them ("1,234"), or null when unavailable or blocked. */
export async function fetchWeeklyViews(origin: string, fetcher: typeof fetch = fetch): Promise<string | null> {
  try {
    const response = await fetcher(`${origin}/counter/TOTAL.json?start=week`, { credentials: 'omit' })
    if (!response.ok) return null
    const body: unknown = await response.json()
    const count = (body as { count?: unknown } | null)?.count
    return typeof count === 'string' && /^[\d,.\s]+$/.test(count) ? count.trim() : null
  } catch {
    return null
  }
}
