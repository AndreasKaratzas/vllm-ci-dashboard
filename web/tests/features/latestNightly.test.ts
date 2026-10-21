import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import { latestNightly } from '@/domain/nightly'
import LatestNightlyCallout from '@/features/nightly/LatestNightlyCallout.vue'
import { installLayoutStubs } from '../components/support'

installLayoutStubs()

const NOW = Date.parse('2026-10-20T12:00:00Z')
const signal = { number: 95829, has_test_results: true, total_groups: 317, state: 'failed', failed_groups: [{ name: 'a' }] }
const failed = (count: number) => Array.from({ length: count }, (_, i) => ({ name: `job ${i}`, state: 'failed' }))
const running = (extra = {}) => ({ number: 96078, created_at: '2026-10-20T06:00:00Z', state: 'failing', has_test_results: false, total_groups: 0, ...extra })
const mountFor = (latest: object, onOpen = vi.fn()) => mount(LatestNightlyCallout, { props: { nightly: latestNightly([latest, signal])!, onOpen } })

describe('LatestNightlyCallout', () => {
  it('names a nightly whose AMD jobs still run, its failures so far and the nightly the counts come from', async () => {
    vi.useFakeTimers({ now: NOW })
    const onOpen = vi.fn()
    const wrapper = mountFor(running({ failed_groups: failed(6) }), onOpen)
    vi.useRealTimers()
    expect(wrapper.get('[data-testid="latest-nightly"]').attributes('data-state')).toBe('awaiting')
    expect(wrapper.text()).toContain('Nightly #96078 is still running its AMD jobs')
    expect(wrapper.text()).toContain('Started 6h ago, with 6 hard failures so far.')
    expect(wrapper.text()).toContain('Counts come from #95829, the newest nightly with test results.')
    await wrapper.get('button').trigger('click')
    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('says final AMD results count while non-AMD jobs keep the build open', () => {
    const wrapper = mountFor({ ...signal, number: 96078, source_state: 'failing', amd_complete: true, non_amd_jobs_active: 1 })
    expect(wrapper.text()).toContain('Nightly #96078: AMD jobs complete · build still running (1 non-AMD job)')
    expect(wrapper.text()).toContain('Every AMD job is final')
    expect(wrapper.text()).not.toContain('Counts come from')
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('gives AMD step progress while the AMD jobs run, and says when Buildkite cancels the build', () => {
    vi.useFakeTimers({ now: NOW })
    const progress = mountFor(running({ amd_step_count: 234, amd_steps_active: 54, failed_groups: failed(1) }))
    const canceling = mountFor(running({ state: 'canceling' }))
    vi.useRealTimers()
    expect(progress.text()).toContain('Started 6h ago; 180 of 234 AMD steps final, with 1 hard failure so far.')
    expect(canceling.get('[data-testid="latest-nightly"]').attributes('data-state')).toBe('awaiting')
    expect(canceling.text()).toContain('Nightly #96078 is being canceled before its AMD results')
  })

  it('names what keeps an AMD-complete build open: other jobs, a cancel, or Buildkite alone', () => {
    const amdComplete = (extra: object) => mountFor({ ...signal, number: 96078, source_state: 'failing', amd_complete: true, non_amd_jobs_active: 3, ...extra }).text()
    expect(amdComplete({})).toContain('the build stays open only for 3 jobs on other hardware.')
    expect(amdComplete({ source_state: 'canceling' })).toContain("Buildkite is canceling the build's remaining jobs.")
    expect(amdComplete({ non_amd_jobs_active: 0 })).toContain('Buildkite has not marked the build finished yet.')
  })

  it('explains a retry, blocked jobs and a nightly that finished without results', () => {
    expect(mountFor(running({ active_retry: true })).text()).toContain('Nightly #96078 is re-running an AMD job')
    const blocked = mountFor(running({ state: 'failed', test_jobs_blocked: 4, failed_groups: failed(1) }))
    expect(blocked.text()).toContain('4 AMD test jobs never started.')
    expect(blocked.find('button').exists()).toBe(false)
    const none = mount(LatestNightlyCallout, { props: { nightly: latestNightly([running({ state: 'failed', failed_groups: failed(2) })])!, onOpen: () => {} } })
    expect(none.text()).toContain('Nightly #96078 has no AMD test results')
    expect(none.text()).toContain('Finished without parsed AMD test results; 2 job variants failed.')
    expect(none.text()).toContain('No retained nightly has test results yet.')
    expect(none.get('button').text()).toBe('Failed jobs')
  })
})
