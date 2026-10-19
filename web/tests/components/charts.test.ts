import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import Donut from '@/components/charts/Donut.vue'
import LineChart from '@/components/charts/LineChart.vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import Timeline from '@/components/charts/Timeline.vue'
import { escapeHtml, tooltipHtml } from '@/components/charts/tooltip'
import { SERIES, statusVar } from '@/lib/palette'
import { installLayoutStubs, settle, text } from './support'

// jsdom cannot lay out Unovis SVG; the chart contracts (labels, legend, Inspect path) live around it.
vi.mock('@unovis/vue', async () => {
  const { defineComponent, h } = await import('vue')
  const stub = (name: string) => defineComponent({ name, inheritAttrs: false, setup: (_, { slots }) => () => h('div', { 'data-vis': name }, slots.default?.()) })
  const names = ['VisXYContainer', 'VisSingleContainer', 'VisArea', 'VisLine', 'VisScatter', 'VisAxis', 'VisStackedBar', 'VisGroupedBar', 'VisDonut', 'VisTooltip', 'VisCrosshair']
  return Object.fromEntries(names.map((name) => [name, stub(name)]))
})

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

type Row = { day: number; rate: number; hard: number; soft: number }
const DAY = 86_400_000
const rows: Row[] = [0, 1, 2].map((i) => ({ day: Date.UTC(2026, 9, 10 + i), rate: 97 + i, hard: 3 - i, soft: i }))
const rate = [{ key: 'rate', label: 'Pass rate', color: SERIES.primary }]
const failures = [
  { key: 'hard', label: 'Hard', color: statusVar('hard') },
  { key: 'soft', label: 'Soft', color: statusVar('soft') },
]

/** Opens a chart's Inspect menu from the keyboard path and picks one entry. */
async function inspect(entry: number) {
  const trigger = document.querySelector<HTMLButtonElement>('[data-slot=chart-inspect]')
  expect(trigger, 'Inspect control').not.toBeNull()
  trigger!.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, ctrlKey: false, pointerType: 'mouse' }))
  trigger!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
  await settle()
  const items = [...document.querySelectorAll<HTMLElement>('[role=menuitem]')]
  items[entry]!.click()
  await settle()
  return items.map((item) => text(item))
}

describe('tooltip', () => {
  it('escapes every value it renders', () => {
    expect(escapeHtml(`<img src=x onerror="a">&'`)).toBe('&lt;img src=x onerror=&quot;a&quot;&gt;&amp;&#39;')
    const html = tooltipHtml({ title: '<b>#12</b>', rows: [{ label: 'a<b', value: 1234, color: 'var(--series-1)' }], hint: 'Click for evidence' })
    expect(html).not.toContain('<b>')
    expect(html).toContain('&lt;b&gt;#12&lt;/b&gt;')
    expect(html).toContain('1,234')
    expect(html).toContain('Click for evidence')
  })
})

describe('AreaChart', () => {
  it('offers a keyboard Inspect path that selects the row', async () => {
    const picked: number[] = []
    const wrapper = mount(AreaChart<Row>, {
      props: { rows, x: (r: Row) => r.day, series: rate, label: 'Nightly pass rate', onSelect: (_r: Row, i: number) => picked.push(i) },
      attachTo: document.body,
    })
    await settle()
    expect(wrapper.find('[role=img]').attributes('aria-label')).toBe('Nightly pass rate')
    expect(wrapper.find('[role=img]').classes()).toContain('cursor-pointer')
    const entries = await inspect(2)
    expect(entries).toEqual(['Oct 10 97', 'Oct 11 98', 'Oct 12 99'])
    expect(picked).toEqual([2])
    wrapper.unmount()
  })

  it('limits the plot to the chosen trailing range', async () => {
    const wrapper = mount(AreaChart<Row>, {
      props: { rows, x: (r: Row) => r.day, series: rate, label: 'Rate', ranges: [{ value: 2, label: 'Last 2' }, { value: 0, label: 'All' }], range: 2, onSelect: () => {} },
      attachTo: document.body,
    })
    await settle()
    const toggles = wrapper.findAll('[data-slot=toggle-group-item]')
    expect(toggles.map((r) => r.text())).toEqual(['Last 2', 'All'])
    expect(toggles[0]!.attributes('data-state')).toBe('on')
    expect(toggles[1]!.classes()).toContain('cursor-pointer')
    expect(await inspect(0)).toEqual(['Oct 11 98', 'Oct 12 99'])
    wrapper.unmount()
  })

  it('has no Inspect control when nothing is clickable', () => {
    const wrapper = mount(AreaChart<Row>, { props: { rows, x: (r: Row) => r.day, series: rate, label: 'Rate' } })
    expect(wrapper.find('[data-slot=chart-inspect]').exists()).toBe(false)
    expect(wrapper.find('[role=img]').classes()).not.toContain('cursor-pointer')
  })
})

describe('LineChart', () => {
  it('lists every point in the Inspect menu', async () => {
    const picked: Row[] = []
    const wrapper = mount(LineChart<Row>, {
      props: { rows, x: (r: Row) => r.day, series: rate, label: 'Latency', rowLabel: (r: Row) => `#${r.rate}`, onSelect: (r: Row) => picked.push(r) },
      attachTo: document.body,
    })
    await settle()
    expect(await inspect(1)).toEqual(['#97 97', '#98 98', '#99 99'])
    expect(picked).toEqual([rows[1]])
    wrapper.unmount()
  })
})

describe('BarChart', () => {
  it('shows a legend for several series and inspects a category', async () => {
    const picked: number[] = []
    const wrapper = mount(BarChart<Row>, {
      props: { rows, category: (r: Row) => `#${r.day / DAY}`, series: failures, label: 'Failures', onSelect: (_r: Row, i: number) => picked.push(i) },
      attachTo: document.body,
    })
    await settle()
    expect(text(wrapper.find('[data-slot=chart-legend]').element)).toBe('Hard Soft')
    const entries = await inspect(0)
    expect(entries[0]).toBe(`#${rows[0]!.day / DAY} 3 · 0`)
    expect(picked).toEqual([0])
    wrapper.unmount()
  })
})

describe('Donut', () => {
  const slices = [
    { key: 'mi355', label: 'MI355', value: 3, color: SERIES.primary },
    { key: 'mi300', label: 'MI300', value: 0, color: SERIES.secondary },
  ]

  it('makes each slice reachable as a legend button with value and share', async () => {
    const picked: string[] = []
    const wrapper = mount(Donut, { props: { slices, label: 'Mirrors by hardware', onSelect: (s: { key: string }) => picked.push(s.key) } })
    const buttons = wrapper.findAll('button')
    expect(buttons.map((b) => text(b.element))).toEqual(['MI355 3 100%', 'MI300 0 0%'])
    await buttons[1]!.trigger('click')
    expect(picked).toEqual(['mi300'])
    expect(buttons[1]!.find('.tabular-nums').classes()).toContain('text-muted-foreground')
  })
})

describe('ProportionBar', () => {
  const segments = [
    { key: 'pass', label: 'Passing', count: 6, color: statusVar('pass') },
    { key: 'hard', label: 'Hard', count: 2, color: statusVar('hard') },
    { key: 'soft', label: 'Soft', count: 0, color: statusVar('soft') },
  ]

  it('sizes filled segments by share and summarizes for screen readers', () => {
    const wrapper = mount(ProportionBar, { props: { segments, label: 'Groups' } })
    const bar = wrapper.find('[role=img]')
    expect(bar.attributes('aria-label')).toBe('Groups: Passing 6, Hard 2, Soft 0')
    expect(bar.findAll('span').map((s) => s.attributes('style'))).toEqual([
      'width: 75%; background-color: var(--status-pass);',
      'width: 25%; background-color: var(--status-hard);',
    ])
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('turns legend entries into buttons when selectable and mutes zero counts', async () => {
    const picked: string[] = []
    const wrapper = mount(ProportionBar, { props: { segments, label: 'Groups', max: 16, onSelect: (s: { key: string }) => picked.push(s.key) } })
    expect(wrapper.find('[role=img] span').attributes('style')).toContain('width: 37.5%')
    const buttons = wrapper.findAll('button')
    await buttons[1]!.trigger('click')
    expect(picked).toEqual(['hard'])
    expect(buttons[2]!.find('.tabular-nums').classes()).toContain('text-muted-foreground')
  })
})

describe('Timeline', () => {
  it('lists segments for keyboard selection', async () => {
    const picked: string[] = []
    const segments = [
      { id: 'a', row: 'node-1', start: 0, end: 10, color: statusVar('hard'), title: 'kernels' },
      { id: 'b', row: 'node-2', start: 5, end: 9, color: statusVar('soft'), title: 'models' },
    ]
    const wrapper = mount(Timeline, { props: { segments, label: 'Failing runs', onSelect: (s: { id: string }) => picked.push(s.id) }, attachTo: document.body })
    await settle()
    expect(wrapper.find('[data-testid=timeline]').attributes('data-rows')).toBe('2')
    expect(await inspect(1)).toEqual(['kernels node-1', 'models node-2'])
    expect(picked).toEqual(['b'])
    wrapper.unmount()
  })

  it('renders the empty slot without a chart', () => {
    const wrapper = mount(Timeline, { props: { segments: [], label: 'Failing runs' }, slots: { empty: '<p>No runs</p>' } })
    expect(wrapper.text()).toBe('No runs')
    expect(wrapper.find('[data-testid=timeline]').exists()).toBe(false)
  })
})
