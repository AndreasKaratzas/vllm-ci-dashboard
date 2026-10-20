import { describe, expect, it } from 'vitest'
import { queueRoute, routeFor } from '@/app/oldLinks'

const route = (tab: string, query = '') => routeFor(tab, new URLSearchParams(query))
const queue = (query: string) => queueRoute(new URLSearchParams(query))

describe('old dashboard links', () => {
  it('routes ported views to v2', () => {
    expect(route('projects')).toBe('#/')
    expect(route('ci-health')).toBe('#/health')
    expect(route('ci-health', 'ops_health_view=overview')).toBe('#/health')
    expect(route('ci-health', 'ops_health_view=parity')).toBe('#/health?view=parity')
    // ci_area_regression_watcher links the retired targets view.
    expect(route('ci-health', 'ops_health_view=targets&ops_health_result=non_passing')).toBe('#/health')
    expect(route('ci-health', 'ops_health_view=mirrors')).toBe('#/health?view=mirrors')
    expect(route('ci-health', 'ops_health_view=coverage')).toBe('#/health?view=coverage')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=platform')).toBe('#/health?view=coverage')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=name')).toBe('#/health?view=coverage&sort=name')
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=area')).toBe('#/health?view=coverage&sort=area')
    // Unknown sort modes are ignored.
    expect(route('ci-health', 'ops_health_view=coverage&ops_health_sort=bogus')).toBe('#/health?view=coverage')
  })

  it('routes Perf Eval links with their state', () => {
    expect(route('ci-perf-eval')).toBe('#/perf')
    expect(route('ci-perf-eval', 'ops_perf_view=performance&ops_perf_model=all&ops_perf_device=all')).toBe('#/perf')
    expect(route('ci-perf-eval', 'ops_perf_view=accuracy')).toBe('#/perf?view=accuracy')
    expect(route('ci-perf-eval', 'ops_perf_view=bogus&ops_detail=perf-model-x')).toBe('#/perf')
    expect(route('ci-perf-eval', 'ops_perf_model=MiniMaxAI%2FMiniMax-M2.5&ops_perf_device=mi355x&ops_perf_view=accuracy')).toBe(
      '#/perf?view=accuracy&model=MiniMaxAI%2FMiniMax-M2.5&device=mi355x',
    )
  })

  it('routes Omni CI links with their windows', () => {
    expect(route('ci-omni')).toBe('#/omni')
    expect(route('ci-omni', 'ops_omni_mapping_range=1m&ops_omni_range=72h')).toBe('#/omni?mapping=1m&range=72h')
    expect(route('ci-omni', 'ops_omni_mapping_range=7d&ops_omni_range=24h')).toBe('#/omni')
    expect(route('ci-omni', 'ops_omni_range=3h&ops_omni_age=gte3d')).toBe('#/omni?range=3h')
    // Unknown values fall back to the defaults.
    expect(route('ci-omni', 'ops_omni_mapping_range=2y&ops_omni_range=9h')).toBe('#/omni')
  })

  it('routes the DNS and agent-health analytics views with their state', () => {
    expect(route('ci-analytics', 'ops_analytics_view=dns')).toBe('#/analytics?view=dns')
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_dns_window=3h&ops_analytics_dns_scope=canonical')).toBe(
      '#/analytics?view=dns&dns_window=3h&dns_scope=canonical',
    )
    // Unknown and default values fall back to the defaults, which the route leaves implicit.
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_dns_window=2h&ops_analytics_dns_scope=amd')).toBe('#/analytics?view=dns')
    expect(route('ci-analytics', 'ops_analytics_view=agent-health')).toBe('#/analytics?view=agent-health')
    expect(
      route(
        'ci-analytics',
        'ops_analytics_view=agent-health&ops_agent_window=30d&ops_agent_gpu=MI355&ops_agent_node=gpu9138&ops_agent_cofail=60&ops_agent_excl_cancel=0&ops_agent_nightly=1&ops_agent_signal=hard',
      ),
    ).toBe(
      '#/analytics?view=agent-health&agent_window=30d&agent_gpu=MI355&agent_node=gpu9138&agent_cofail=60&agent_excl_cancel=0&agent_nightly=1&agent_signal=hard',
    )
    expect(route('ci-analytics', 'ops_analytics_view=agent-health&ops_agent_window=2d&ops_agent_cofail=45&ops_agent_signal=x')).toBe(
      '#/analytics?view=agent-health',
    )
    // A test-group search always opens AMD test health.
    expect(route('ci-analytics', 'ops_analytics_view=dns&ops_analytics_search=foo')).not.toBe('#/analytics?view=dns')
  })

  it('leaves unrouted views to the old page', () => {
    expect(route('ci-analytics', 'ops_analytics_view=reliability')).toBeNull()
  })

  it('routes ported CI Analytics views to v2', () => {
    expect(route('ci-analytics')).toBe('#/analytics')
    expect(route('ci-analytics', 'ops_analytics_view=groups')).toBe('#/analytics')
    // ci_main_failure_watcher links groups with a retired pipeline parameter.
    expect(route('ci-analytics', 'ops_analytics_view=groups&ops_analytics_pipeline=ci')).toBe('#/analytics')
    // ops_analytics_search always forces the groups view.
    expect(route('ci-analytics', 'ops_analytics_search=Batch%20Invariance')).toBe('#/analytics')
    // amd_duration_regression_watcher links the latency view.
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBe('#/analytics?view=latency')
    expect(route('ci-analytics', 'ops_analytics_view=nightlies')).toBe('#/analytics?view=nightlies')
  })
})

describe('old Queue DNS links', () => {
  it('migrate to the DNS analytics view with their window and scope', () => {
    expect(queue('ops_queue_view=dns')).toBe('#/analytics?view=dns')
    expect(queue('ops_queue_view=dns&ops_queue_dns_window=3h&ops_queue_scope=canonical')).toBe(
      '#/analytics?view=dns&dns_window=3h&dns_scope=canonical',
    )
    expect(queue('ops_queue_view=dns&ops_queue_dns_window=24h&ops_queue_scope=all')).toBe('#/analytics?view=dns')
    expect(queue('ops_queue_view=dns&ops_queue_dns_window=5h')).toBe('#/analytics?view=dns')
  })

  it('leave other Queue views to the old page', () => {
    expect(queue('')).toBeNull()
    expect(queue('ops_queue_view=live')).toBeNull()
  })
})
