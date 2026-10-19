import { describe, expect, it } from 'vitest'
import { queueRoute } from '@/legacy/analytics'

const route = (query: string) => queueRoute(new URLSearchParams(query))

// dashboard-nav.js migrates the retired Queue DNS view to CI Analytics.
describe('legacy Queue DNS links', () => {
  it('migrate to the DNS analytics view with the window and scope classic keeps', () => {
    expect(route('ops_queue_view=dns')).toBe('#/analytics?view=dns')
    expect(route('ops_queue_view=dns&ops_queue_dns_window=3h&ops_queue_scope=canonical')).toBe(
      '#/analytics?view=dns&dns_window=3h&dns_scope=canonical',
    )
    expect(route('ops_queue_view=dns&ops_queue_dns_window=24h&ops_queue_scope=all')).toBe('#/analytics?view=dns')
    expect(route('ops_queue_view=dns&ops_queue_dns_window=5h')).toBe('#/analytics?view=dns')
  })

  it('leave other Queue views to the classic page', () => {
    expect(route('')).toBeNull()
    expect(route('ops_queue_view=live')).toBeNull()
  })
})
