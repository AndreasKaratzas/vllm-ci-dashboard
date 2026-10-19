import { describe, expect, it } from 'vitest'
import { v2RouteFor } from '@/legacyLinks'

const route = (tab: string, query = '') => v2RouteFor(tab, new URLSearchParams(query))

describe('legacy dashboard links', () => {
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
    // Classic ignores unknown sort modes.
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
    // classic syncRouteState falls back to the defaults for unknown values.
    expect(route('ci-omni', 'ops_omni_mapping_range=2y&ops_omni_range=9h')).toBe('#/omni')
  })

  it('leaves unported views to the classic page', () => {
    expect(route('ci-analytics', 'ops_analytics_view=latency')).toBeNull()
  })
})
