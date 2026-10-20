import { mount } from '@vue/test-utils'
import { describe, expect, it, vi } from 'vitest'
import PendingNightlyCallout from '@/features/nightly/PendingNightlyCallout.vue'
import { installLayoutStubs } from '../components/support'

installLayoutStubs()

const pending = (extra = {}) => ({ latest: { number: 96078 }, signal: { number: 95829 }, running: true, hard: 6, soft: 0, ...extra })

describe('PendingNightlyCallout', () => {
  it('names the running nightly, its failures so far and the nightly the counts come from', async () => {
    const onOpen = vi.fn()
    const wrapper = mount(PendingNightlyCallout, { props: { pending: pending(), onOpen } })
    expect(wrapper.text()).toContain('Nightly #96078 has no test results yet')
    expect(wrapper.text()).toContain('Buildkite is still running it, with 6 hard failures so far.')
    expect(wrapper.text()).toContain('Counts come from #95829, the newest nightly with test results.')
    await wrapper.get('button').trigger('click')
    expect(onOpen).toHaveBeenCalledOnce()
  })

  it('offers nothing to open without failures and says when no nightly has results', () => {
    const wrapper = mount(PendingNightlyCallout, { props: { pending: pending({ running: false, hard: 0, signal: undefined }), onOpen: () => {} } })
    expect(wrapper.text()).toContain('Nightly #96078 has no test results')
    expect(wrapper.text()).toContain('It finished without parsed test-group results.')
    expect(wrapper.text()).toContain('No retained nightly has test results yet.')
    expect(wrapper.find('button').exists()).toBe(false)
  })
})
