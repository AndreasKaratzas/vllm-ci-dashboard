import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import DeltaBadge from '@/components/stat/DeltaBadge.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { settle, testRouter, text } from './support'

afterEach(() => {
  document.body.innerHTML = ''
})

const interactive = (el: Element) => el.querySelectorAll('a, button, [tabindex], input, select')

describe('KpiCard', () => {
  it('renders label, value, unit and footnote', () => {
    const wrapper = mount(KpiCard, { props: { label: 'Median latency', value: '41', unit: 'min', footnote: 'Last 30 nightlies' } })
    expect(text(wrapper.find('[data-slot=kpi-value]').element)).toBe('41 min')
    expect(wrapper.text()).toContain('Median latency')
    expect(wrapper.text()).toContain('Last 30 nightlies')
    expect(interactive(wrapper.element)).toHaveLength(0)
    expect(wrapper.classes()).not.toContain('group/kpi')
  })

  it('renders a missing value as a muted dash, never a fake number', () => {
    const wrapper = mount(KpiCard, { props: { label: 'Pass rate', value: null, unit: '%' } })
    const value = wrapper.find('[data-slot=kpi-value]')
    expect(text(value.element)).toBe('—')
    expect(value.classes()).toContain('text-muted-foreground')
  })

  it('tweens numbers to the formatted final value', async () => {
    const wrapper = mount(KpiCard, { props: { label: 'Failures', value: 1234 } })
    await new Promise((resolve) => setTimeout(resolve, 600))
    expect(text(wrapper.find('[data-slot=kpi-value]').element)).toBe('1,234')
  })

  it('makes the whole card one stretched router link with hover and focus states', async () => {
    const router = await testRouter()
    const wrapper = mount(KpiCard, {
      props: { label: 'Nightly pass rate', value: '97.2%', delta: { value: -1.4, goodWhen: 'up', text: '-1.4 pts' }, action: { label: 'Open nightlies', to: '/health' } },
      global: { plugins: [router] },
    })
    const controls = interactive(wrapper.element)
    expect(controls).toHaveLength(1)
    const link = controls[0]!
    expect(link.tagName).toBe('A')
    expect(link.getAttribute('href')).toBe('/health')
    expect(link.className).toContain('after:absolute')
    expect(link.className).toContain('after:inset-0')
    expect(text(link)).toBe('Nightly pass rate : Open nightlies')
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['relative', 'group/kpi', 'hover:border-foreground/20']))
    expect(wrapper.find('svg[class*="group-hover/kpi:translate-x-0.5"]').exists()).toBe(true)
  })

  it('uses an external link or a button for the other action kinds', async () => {
    const opened: string[] = []
    const external = mount(KpiCard, { props: { label: 'Builds', value: 3, action: { label: 'Open Buildkite', href: 'https://buildkite.com/x' } } })
    const a = external.find('a')
    expect(a.attributes('target')).toBe('_blank')
    expect(a.attributes('rel')).toBe('noopener')
    const button = mount(KpiCard, { props: { label: 'Open issues', value: 3, action: { label: 'Show issues', onOpen: () => opened.push('x') } } })
    await button.find('button').trigger('click')
    expect(opened).toEqual(['x'])
    expect(interactive(button.element)).toHaveLength(1)
  })

  it('draws the ring variant from the ratio with a threshold tone', async () => {
    const wrapper = mount(KpiCard, { props: { label: 'Parity', value: 'Upstream', variant: 'ring', ratio: { current: 96, total: 100 } } })
    await settle()
    expect(wrapper.find('[role=img]').attributes('aria-label')).toBe('Parity: 96.0%')
    expect(wrapper.find('circle.stroke-status-soft').exists()).toBe(true)
    expect(wrapper.text()).toContain('96 / 100')
  })

  it('shows a sparkline when history exists', () => {
    const wrapper = mount(KpiCard, { props: { label: 'Failures', value: 4, spark: [1, 3, 2, 4] } })
    expect(wrapper.find('[data-slot=sparkline]').attributes('aria-label')).toBe('Failures trend over 4 points')
    expect(mount(KpiCard, { props: { label: 'Failures', value: 4, spark: [4] } }).find('[data-slot=sparkline]').exists()).toBe(false)
  })
})

describe('DeltaBadge', () => {
  it('states direction and whether the change is better or worse', () => {
    const worse = mount(DeltaBadge, { props: { delta: { value: 3, goodWhen: 'down' } } })
    expect(worse.attributes('aria-label')).toBe('up +3, worse')
    expect(worse.attributes('data-direction')).toBe('up')
    expect(worse.classes()).toContain('text-status-hard-fg')
    const better = mount(DeltaBadge, { props: { delta: { value: -2, goodWhen: 'down', text: '-2' } } })
    expect(better.attributes('aria-label')).toBe('down -2, better')
    expect(better.classes()).not.toContain('text-status-hard-fg')
    expect(mount(DeltaBadge, { props: { delta: { value: 0 } } }).attributes('aria-label')).toBe('unchanged 0')
  })
})

describe('Sparkline', () => {
  it('draws a line through every point and marks the last one', () => {
    const wrapper = mount(Sparkline, { props: { values: [0, 10, 5], label: 'trend' } })
    const line = wrapper.findAll('path')[1]!.attributes('d')
    expect(line).toBe('M0.00,30.00L50.00,2.00L100.00,16.00')
    expect(wrapper.find('path').attributes('fill')).toBe('var(--series-1)')
    expect(wrapper.find('span').attributes('style')).toContain('left: 100%')
  })
})

describe('StatGrid', () => {
  it('lays KPIs out two-up on mobile', () => {
    const wrapper = mount(StatGrid, { props: { cols: 4 } })
    expect(wrapper.classes()).toEqual(expect.arrayContaining(['grid-cols-2', '@5xl/main:grid-cols-4', '*:min-w-0']))
  })
})
