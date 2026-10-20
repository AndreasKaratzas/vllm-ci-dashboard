import { mount } from '@vue/test-utils'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { STATUS } from './publication'
import { elapse, installServer } from './server'

let server: ReturnType<typeof installServer>

beforeEach(() => {
  vi.useFakeTimers({ toFake: ['setTimeout', 'clearTimeout', 'setInterval', 'clearInterval', 'Date'] })
  vi.setSystemTime(new Date('2026-10-19T10:00:00Z'))
  vi.resetModules()
  server = installServer()
})

afterEach(() => {
  vi.useRealTimers()
  vi.unstubAllGlobals()
})

async function mountBanner() {
  const { default: PublicationBanner } = await import('@/components/layout/PublicationBanner.vue')
  const wrapper = mount(PublicationBanner)
  await elapse(2000, 8)
  return wrapper
}

describe('PublicationBanner', () => {
  it('stays hidden while the publication is current', async () => {
    // The live payload shape (publication_status.json, schema 1).
    server.set(STATUS, {
      affected_surface_count: 0,
      affected_surfaces: [],
      degraded_since: null,
      fallback_surface_count: 0,
      fresh_degraded_surface_count: 0,
      generated_at: '2026-10-19T09:55:00Z',
      mode: 'current',
      publication_blocked: false,
      schema_version: 1,
      status: 'healthy',
      uses_fallback: false,
    })
    const wrapper = await mountBanner()
    expect(wrapper.find('[data-testid="publication-banner"]').exists()).toBe(false)
    wrapper.unmount()
  })

  it('warns that the status is unavailable when the status file cannot be read', async () => {
    server.set(STATUS, { status: 503 })
    const wrapper = await mountBanner()
    expect(wrapper.get('[data-testid="publication-banner"]').text()).toContain('Publication status unavailable')
    wrapper.unmount()
  })
})
