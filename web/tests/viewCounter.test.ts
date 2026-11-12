import { describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import ViewCount from '@/components/layout/ViewCount.vue'
import { counterOrigin, fetchWeeklyViews, trackViews } from '@/data/viewCounter'
import { defineComponent, h } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'
import { settle } from './components/support'

const config = { goatcounter_code: 'vllm-ci', site_url: 'https://andreaskaratzas.github.io/vllm-ci-dashboard/' }
const live = { origin: 'https://andreaskaratzas.github.io', pathname: '/vllm-ci-dashboard/' }

describe('view counter', () => {
  it('counts only the published site, never previews, local dev, automation or an unset code', () => {
    expect(counterOrigin(live, config)).toBe('https://vllm-ci.goatcounter.com')
    expect(counterOrigin({ ...live, pathname: '/vllm-ci-dashboard/index.html' }, config)).toBe('https://vllm-ci.goatcounter.com')
    expect(counterOrigin({ ...live, pathname: '/vllm-ci-dashboard/pr-preview/pr-12/' }, config)).toBeNull()
    expect(counterOrigin({ origin: 'http://127.0.0.1:4173', pathname: '/' }, config)).toBeNull()
    expect(counterOrigin(live, config, true)).toBeNull()
    expect(counterOrigin(live, { ...config, goatcounter_code: '' })).toBeNull()
  })

  it('counts the first load and route changes, not query-only filter changes', async () => {
    const Blank = defineComponent({ render: () => h('div') })
    const router = createRouter({
      history: createMemoryHistory(),
      routes: [
        { path: '/', component: Blank },
        { path: '/health', component: Blank },
      ],
    })
    const sent: URL[] = []
    trackViews(router, 'https://vllm-ci.goatcounter.com', (url) => sent.push(new URL(url)))
    for (const path of ['/', '/health', '/health?view=parity', '/']) await router.push(path)
    expect(sent.map((url) => url.searchParams.get('p'))).toEqual(['/', '/health', '/'])
    expect(sent.every((url) => url.href.startsWith('https://vllm-ci.goatcounter.com/count?'))).toBe(true)
  })

  it('shows the weekly count and hides itself when the counter API is blocked or malformed', async () => {
    const ok = (body: unknown) => (async () => new Response(JSON.stringify(body))) as typeof fetch
    const asked: string[] = []
    await fetchWeeklyViews('https://x.goatcounter.com', (async (url: string) => {
      asked.push(url)
      return new Response(JSON.stringify({ count: '7' }))
    }) as unknown as typeof fetch)
    expect(asked).toEqual(['https://x.goatcounter.com/counter/TOTAL.json?start=week'])
    expect(await fetchWeeklyViews('https://x.goatcounter.com', ok({ count: '1,234' }))).toBe('1,234')
    expect(await fetchWeeklyViews('https://x.goatcounter.com', ok({ count: '<b>' }))).toBeNull()
    expect(await fetchWeeklyViews('https://x.goatcounter.com', (async () => new Response('', { status: 403 })) as typeof fetch)).toBeNull()
    expect(await fetchWeeklyViews('https://x.goatcounter.com', (async () => { throw new TypeError('blocked') }) as typeof fetch)).toBeNull()

    vi.stubGlobal('fetch', ok({ count: '1,234' }))
    const shown = mount(ViewCount, { props: { origin: 'https://x.goatcounter.com' } })
    await settle()
    expect(shown.find('[data-slot=view-count]').text()).toBe('1,234 views this week')
    vi.stubGlobal('fetch', async () => {
      throw new TypeError('blocked')
    })
    const blocked = mount(ViewCount, { props: { origin: 'https://x.goatcounter.com' } })
    await settle()
    expect(blocked.find('[data-slot=view-count]').exists()).toBe(false)
    vi.unstubAllGlobals()
  })
})
