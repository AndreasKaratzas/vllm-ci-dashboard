import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import PageHeader from '@/components/layout/PageHeader.vue'
import SubNav from '@/components/layout/SubNav.vue'
import { installLayoutStubs, settle, testRouter, text } from './support'

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

describe('PageHeader', () => {
  it('renders the title as the page heading with description, freshness and actions', () => {
    const wrapper = mount(PageHeader, {
      props: { title: 'CI Health', description: 'AMD nightly coverage', freshness: { text: 'Collected 12 min ago', tone: 'pass', detail: 'ci_health.json' } },
      slots: { actions: '<button>Export</button>' },
    })
    expect(wrapper.find('h1').text()).toBe('CI Health')
    expect(text(wrapper.find('p').element)).toBe('AMD nightly coverage')
    const chip = wrapper.find('[data-slot=freshness]')
    expect(chip.attributes('title')).toBe('ci_health.json')
    expect(chip.find('[aria-hidden=true]').classes()).toContain('bg-status-pass')
    expect(wrapper.find('button').text()).toBe('Export')
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
