import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h, ref, type ShallowRef } from 'vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import SubNav from '@/components/layout/SubNav.vue'
import { currentSubView, pageViews, subViewIds, subViewQuery } from '@/components/layout/navigation'
import { provideSubviewHeader, useSubviewHeader, type SubviewHeader } from '@/composables/useSubviewHeader'
import { installLayoutStubs, settle, testRouter, text } from './support'

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

describe('PageHeader', () => {
  it('renders the title as the page heading with description, freshness and actions', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'CI Health', description: 'AMD nightly coverage', freshness: { text: 'Collected 12 min ago', tone: 'soft' } },
      slots: { actions: '<button>Export</button>' },
    })
    expect(wrapper.find('h1').text()).toBe('CI Health')
    expect(text(wrapper.find('p').element)).toBe('AMD nightly coverage')
    const chip = wrapper.find('[data-slot=freshness]')
    expect(chip.element.tagName).not.toBe('BUTTON')
    expect(chip.find('[aria-hidden=true]').classes()).toContain('bg-status-soft')
    expect(text(chip.element)).toBe('Collected 12 min ago : Past target')
    expect(chip.find('.sr-only').exists()).toBe(true)
    expect(wrapper.find('button').text()).toBe('Export')
  })

  it('opens the freshness detail from a keyboard-reachable button', async () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'CI Health', freshness: { text: 'Collected 3 h ago', tone: 'hard', detail: 'ci_health.json, target 2 h' } },
      attachTo: document.body,
    })
    const chip = wrapper.find('[data-slot=freshness]')
    expect(chip.element.tagName).toBe('BUTTON')
    expect(text(chip.element)).toBe('Collected 3 h ago : Stale')
    await chip.trigger('click')
    await settle()
    expect(text(document.querySelector('[data-slot=freshness-detail]'))).toBe('Stale ci_health.json, target 2 h')
    wrapper.unmount()
  })

  it('puts methodology behind an About popover only when given', async () => {
    expect(mount(PageHeader, { props: { title: 'Perf' } }).find('button').exists()).toBe(false)
    const wrapper = mount(PageHeader, { props: { title: 'Perf' }, slots: { about: '<p>Method</p>' }, attachTo: document.body })
    await wrapper.find('button[aria-label="About this view"]').trigger('click')
    await settle()
    expect(text(document.querySelector('[data-slot=popover-content]'))).toBe('About Perf Method')
    wrapper.unmount()
  })
})

describe('SubNav', () => {
  const items = [
    { value: 'overview', label: 'Overview' },
    { value: 'parity', label: 'Parity', count: 3 },
  ]

  it('links each section through the query and marks the current one', async () => {
    const router = await testRouter('/health?view=parity&group=x')
    const wrapper = mount(SubNav, { props: { items, defaultValue: 'overview' }, global: { plugins: [router] } })
    const links = wrapper.findAll('a')
    expect(wrapper.find('nav').attributes('aria-label')).toBe('Sections')
    expect(links.map((a) => a.attributes('href'))).toEqual(['/health?group=x', '/health?view=parity&group=x'])
    expect(links[1]!.attributes('aria-current')).toBe('page')
    expect(links[0]!.attributes('aria-current')).toBeUndefined()
    expect(links[1]!.classes()).toContain('border-brand')
    expect(text(links[1]!.element)).toBe('Parity 3')
  })

  it('falls back to the default for unknown values and gives links hover states', async () => {
    const router = await testRouter('/health?view=retired')
    const wrapper = mount(SubNav, { props: { items, defaultValue: 'overview' }, global: { plugins: [router] } })
    const first = wrapper.findAll('a')[0]!
    expect(first.attributes('aria-current')).toBe('page')
    expect(first.classes()).toEqual(expect.arrayContaining(['hover:text-foreground', 'focus-visible:ring-3']))
  })
})

describe('SegmentedSelect', () => {
  const items = [
    { value: '7d', label: '7d', title: 'Last 7 days' },
    { value: '30d', label: '30d', title: 'Last 30 days' },
  ]

  it('offers the same choice as a toggle group from sm up and a select below, under one label', () => {
    const wrapper = mount(SegmentedSelect, { props: { label: 'Window', items, modelValue: '7d' } })
    const group = wrapper.find('[role=group]')
    expect(group.attributes('aria-label')).toBe('Window')
    expect(group.classes()).toEqual(expect.arrayContaining(['hidden', 'sm:flex']))
    const toggles = wrapper.findAll('[data-slot=toggle-group-item]')
    expect(toggles.map((t) => [t.text(), t.attributes('title'), t.attributes('data-state')])).toEqual([
      ['7d', 'Last 7 days', 'on'],
      ['30d', 'Last 30 days', 'off'],
    ])
    const select = wrapper.find('[role=combobox]')
    expect(select.attributes('aria-label')).toBe('Window')
    expect(select.classes()).toContain('sm:hidden')
    expect(wrapper.find('[data-slot=segmented-select] > span').text()).toBe('Window')
  })

  it('updates the model from a toggle and never clears it', async () => {
    const wrapper = mount(SegmentedSelect, { props: { label: 'Window', items, modelValue: '7d' } })
    await wrapper.findAll('[data-slot=toggle-group-item]')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['30d']])
    await wrapper.setProps({ modelValue: '30d' })
    await wrapper.findAll('[data-slot=toggle-group-item]')[1]!.trigger('click')
    expect(wrapper.emitted('update:modelValue')).toEqual([['30d']])
  })

  it('can leave the visible label to the surrounding control', () => {
    const wrapper = mount(SegmentedSelect, { props: { label: 'Chart range', items, modelValue: '7d', showLabel: false } })
    expect(wrapper.text()).not.toContain('Chart range')
    expect(wrapper.find('[role=group]').attributes('aria-label')).toBe('Chart range')
  })
})

describe('useSubviewHeader', () => {
  function host(child: ReturnType<typeof defineComponent>) {
    let header!: ShallowRef<SubviewHeader | null>
    const show = ref(true)
    const Page = defineComponent({
      setup() {
        header = provideSubviewHeader()
        return () => h('div', show.value ? [h(child)] : [])
      },
    })
    return { wrapper: mount(Page), header: () => header.value, show }
  }

  it('publishes the mounted sub-view header, follows its changes, and clears it on unmount', async () => {
    const text = ref('Collected 5m ago')
    const Child = defineComponent({
      setup() {
        useSubviewHeader(() => ({ freshness: { text: text.value, tone: 'pass', detail: 'd' } }))
        return () => h('span')
      },
    })
    const { header, show } = host(Child)
    expect(header()?.freshness?.text).toBe('Collected 5m ago')
    text.value = 'Collected 6m ago'
    await settle()
    expect(header()?.freshness?.text).toBe('Collected 6m ago')
    show.value = false
    await settle()
    expect(header()).toBeNull()
  })

  it('is a no-op outside a page that provides a header', () => {
    const Child = defineComponent({
      setup() {
        useSubviewHeader(() => ({}))
        return () => h('span', 'ok')
      },
    })
    expect(mount(Child).text()).toBe('ok')
  })
})

describe('navigation', () => {
  it('lists the sub-views of every page with sub-views, Omni included', () => {
    expect(pageViews('omni')?.items.map((item) => item.value)).toEqual(['workload', 'live'])
    expect(pageViews('home')).toBeUndefined()
    expect(pageViews(undefined)).toBeUndefined()
    expect(subViewIds('analytics')).toEqual(['groups', 'latency', 'slowdowns', 'nightlies', 'dns', 'agent-health'])
    expect(subViewIds('perf')).toEqual(['performance', 'accuracy'])
  })

  it('resolves the shown sub-view, falling back to the page default', () => {
    expect(currentSubView('omni', { view: 'live' })?.label).toBe('Live queues')
    expect(currentSubView('health', { view: ['parity', 'coverage'] })?.value).toBe('parity')
    expect(currentSubView('health', { view: 'nope' })?.value).toBe('overview')
    expect(currentSubView('home', {})).toBeNull()
  })

  it('keeps the default sub-view URL bare', () => {
    expect(subViewQuery('omni', 'workload')).toEqual({})
    expect(subViewQuery('omni', 'live')).toEqual({ view: 'live' })
    expect(subViewQuery('home', 'anything')).toEqual({})
  })
})
