import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import HwDot from '@/components/feedback/HwDot.vue'
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import StatusDot from '@/components/feedback/StatusDot.vue'
import type { Tone } from '@/lib/tone'
import { installLayoutStubs, settle, text } from './support'

const TONES: Tone[] = ['pass', 'hard', 'soft', 'info', 'neutral', 'not-targeted']

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

describe('StatusBadge', () => {
  it('always renders an icon and a label', () => {
    for (const tone of TONES) {
      const wrapper = mount(StatusBadge, { props: { tone } })
      expect(wrapper.find('svg').exists(), tone).toBe(true)
      expect(wrapper.find('svg').attributes('aria-hidden')).toBe('true')
      expect(text(wrapper.element), tone).toMatch(/\w/)
    }
  })

  it('uses the given label and tints only non-pass tones', () => {
    const hard = mount(StatusBadge, { props: { tone: 'hard', label: 'Failing' } })
    expect(text(hard.element)).toBe('Failing')
    expect(hard.classes()).toContain('bg-status-hard/12')
    const pass = mount(StatusBadge, { props: { tone: 'pass' } })
    expect(text(pass.element)).toBe('Passed')
    expect(pass.classes().some((c) => c.startsWith('bg-status'))).toBe(false)
  })

  it('renders info as neutral, never blue', () => {
    const wrapper = mount(StatusBadge, { props: { tone: 'info', label: 'New' } })
    expect(wrapper.classes()).toContain('bg-muted')
    expect(wrapper.html()).not.toMatch(/(bg|text|border)-info/)
  })
})

describe('StatusDot', () => {
  it('pairs a status dot with its label', () => {
    const wrapper = mount(StatusDot, { props: { tone: 'soft', label: 'Flaky' } })
    expect(wrapper.find('[aria-hidden=true]').classes()).toContain('bg-status-soft')
    expect(text(wrapper.element)).toBe('Flaky')
  })
})

describe('HwDot', () => {
  it('colors by normalized hardware family and shows the family', () => {
    const wrapper = mount(HwDot, { props: { family: 'mi355_8' } })
    expect(wrapper.find('[aria-hidden=true]').attributes('style')).toContain('var(--hw-mi355)')
    expect(text(wrapper.element)).toBe('MI355')
    expect(text(mount(HwDot, { props: { family: 'MI355_DPX', label: 'MI355 DPX' } }).element)).toBe('MI355 DPX')
  })
})

describe('Callout', () => {
  it('announces as status with a tone icon, title and body', () => {
    const wrapper = mount(Callout, { props: { tone: 'soft', title: 'Coverage incomplete' }, slots: { default: '154 of 168 buckets' } })
    expect(wrapper.attributes('role')).toBe('status')
    expect(wrapper.find('svg').exists()).toBe(true)
    expect(text(wrapper.element)).toBe('Coverage incomplete 154 of 168 buckets')
    expect(wrapper.classes()).toContain('bg-status-soft/8')
  })
})

describe('EmptyState', () => {
  it('shows the reason, cause chip and action', () => {
    const wrapper = mount(EmptyState, {
      props: { title: 'No nightly retained', description: 'The collector kept none.', cause: 'Coverage 0/5' },
      slots: { action: '<button>Retry</button>' },
    })
    expect(text(wrapper.element)).toBe('No nightly retained The collector kept none. Coverage 0/5 Retry')
  })
})

describe('AsyncBoundary', () => {
  it('shows a busy skeleton while loading', () => {
    const wrapper = mount(AsyncBoundary, { props: { loading: true, error: null }, slots: { default: '<p>content</p>' } })
    expect(wrapper.find('[aria-busy=true]').exists()).toBe(true)
    expect(wrapper.text()).not.toContain('content')
  })

  it('shows the error with a retry action', async () => {
    const wrapper = mount(AsyncBoundary, { props: { loading: false, error: 'HTTP 503' } })
    expect(wrapper.find('[role=alert]').text()).toContain('HTTP 503')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('retry')).toHaveLength(1)
  })

  it('renders content and offers an update when newer data exists', async () => {
    const wrapper = mount(AsyncBoundary, { props: { loading: false, error: null, stale: true }, slots: { default: '<p>content</p>' } })
    expect(wrapper.text()).toContain('content')
    await wrapper.find('[data-slot=async-stale] button').trigger('click')
    expect(wrapper.emitted('refresh')).toHaveLength(1)
    await wrapper.setProps({ stale: false, refreshing: true })
    expect(wrapper.find('[data-slot=async-refreshing]').text()).toBe('Refreshing')
  })
})

describe('InfoPopover', () => {
  it('opens its methodology on click from a labelled button', async () => {
    const wrapper = mount(InfoPopover, { props: { title: 'Method' }, slots: { default: '<p>How it is counted</p>' }, attachTo: document.body })
    const button = wrapper.find('button')
    expect(button.attributes('aria-label')).toBe('About this view')
    expect(button.classes()).toContain('cursor-pointer')
    await button.trigger('click')
    await settle()
    expect(text(document.querySelector('[data-slot=popover-content]'))).toBe('Method How it is counted')
    wrapper.unmount()
  })
})
