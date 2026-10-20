import { afterEach, describe, expect, it, vi } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import AreaChart from '@/components/charts/AreaChart.vue'
import BarChart from '@/components/charts/BarChart.vue'
import Donut from '@/components/charts/Donut.vue'
import LineChart from '@/components/charts/LineChart.vue'
import Heatmap from '@/components/charts/Heatmap.vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import Timeline from '@/components/charts/Timeline.vue'
import NightlyMovementChart from '@/features/nightly/NightlyMovementChart.vue'
import { barValueTicks, distinctTicks, niceTicks, shortDay, timeTicks, useSeriesInteraction } from '@/components/charts/chart'
import { useDragRange } from '@/components/charts/dragRange'
import { escapeHtml, tooltipHtml } from '@/components/charts/tooltip'
import { SERIES, statusVar } from '@/lib/palette'
import { installLayoutStubs, settle, text } from './support'

// jsdom cannot lay out Unovis SVG; the chart contracts (labels, legend, Inspect path) live around it.
vi.mock('@unovis/vue', async () => {
  const { defineComponent, h } = await import('vue')
  const stub = (name: string) => defineComponent({ name, inheritAttrs: false, setup: (_, { slots }) => () => h('div', { 'data-vis': name }, slots.default?.()) })
  const names = ['VisXYContainer', 'VisSingleContainer', 'VisArea', 'VisLine', 'VisPlotband', 'VisScatter', 'VisAxis', 'VisStackedBar', 'VisGroupedBar', 'VisDonut', 'VisTooltip', 'VisCrosshair']
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
    const html = tooltipHtml({
      title: '<b>#12</b>',
      rows: [
        { label: '<i>shard</i>', value: 1234, color: 'var(--series-1)' },
        { label: 'note', value: '<u>x</u>' },
      ],
      hint: '<s>hint</s>',
    })
    for (const tag of ['<b>', '<i>', '<u>', '<s>']) expect(html).not.toContain(tag)
    expect(html).toContain('&lt;b&gt;#12&lt;/b&gt;')
    expect(html).toContain('&lt;i&gt;shard&lt;/i&gt;')
    expect(html).toContain('&lt;u&gt;x&lt;/u&gt;')
    expect(html).toContain('&lt;s&gt;hint&lt;/s&gt;')
    expect(html).toContain('1,234')
  })
})

describe('distinctTicks', () => {
  it('never repeats a label across a short domain', () => {
    const start = Date.UTC(2026, 8, 20, 2)
    expect(distinctTicks([start, start + 6 * 3_600_000], 5, shortDay)).toEqual([start])
    const days = distinctTicks([Date.UTC(2026, 8, 20), Date.UTC(2026, 8, 24)], 5, shortDay)
    expect(days.map(shortDay)).toEqual(['Sep 20', 'Sep 21', 'Sep 22', 'Sep 23', 'Sep 24'])
  })
})

describe('timeTicks', () => {
  const at = (iso: string) => Date.parse(iso)
  it('ticks on round UTC times and names the day at midnight', () => {
    const { values, label } = timeTicks([at('2026-10-16T05:29:00Z'), at('2026-10-19T05:09:00Z')], 8)
    expect(values.map(label)).toEqual(['12:00', 'Oct 17', '12:00', 'Oct 18', '12:00', 'Oct 19'])
    expect(new Set(values.slice(1).map((v, i) => v - values[i]!))).toEqual(new Set([12 * 3_600_000]))
  })

  it('ticks on round minutes when zoomed into one event', () => {
    const { values, label } = timeTicks([at('2026-10-14T08:14:00Z'), at('2026-10-14T08:52:00Z')], 6)
    expect(values.map(label)).toEqual(['08:20', '08:30', '08:40', '08:50'])
  })

  it('switches to whole days on long windows', () => {
    const { values, label } = timeTicks([at('2026-09-20T07:00:00Z'), at('2026-10-19T07:00:00Z')], 6)
    expect(values.map(label)).toEqual(['Sep 24', 'Oct 1', 'Oct 8', 'Oct 15'])
  })
})

describe('crosshair wiring', () => {
  // A hyphenated `on-` binding reaches Unovis as an unknown key and is never called.
  it('hands the crosshair move handler to Unovis under its config name', async () => {
    for (const Chart of [AreaChart, LineChart]) {
      const wrapper = mount(Chart as typeof AreaChart<Row>, { props: { rows, x: (r: Row) => r.day, series: rate, label: 'Rate', onSelect: () => {} } })
      const crosshair = wrapper.findComponent({ name: 'VisCrosshair' })
      expect(typeof crosshair.vm.$attrs.onCrosshairMove).toBe('function')
      expect(crosshair.vm.$attrs['on-crosshair-move']).toBeUndefined()
      wrapper.unmount()
    }
  })
})

describe('useSeriesInteraction', () => {
  it('forgets the hovered row once the pointer leaves the plot', () => {
    const picked: number[] = []
    const chart = useSeriesInteraction<Row>({
      rows: () => rows,
      series: () => rate,
      title: (r) => String(r.day),
      format: String,
      hint: () => undefined,
      onSelect: () => (_r, i) => picked.push(i),
    })
    chart.track(rows[1]!.day, rows[1])
    chart.selectHovered()
    chart.track(undefined, undefined)
    chart.selectHovered()
    expect(picked).toEqual([1])
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

describe('NightlyMovementChart', () => {
  const nightly = (number: number, counts: { new?: number; recurring?: number; fixed?: number }) => ({
    number,
    created_at: `2026-10-0${number % 10}T06:00:00Z`,
    failure_movement: {
      policy_id: 'observed-failure-movement-v1',
      available: true,
      new: Array.from({ length: counts.new ?? 0 }, () => ({})),
      recurring: Array.from({ length: counts.recurring ?? 0 }, () => ({})),
      fixed: Array.from({ length: counts.fixed ?? 0 }, () => ({})),
    },
  })

  it('draws and lists only the movements that happened', async () => {
    const wrapper = mount(NightlyMovementChart, { props: { builds: [nightly(2, { new: 2 }), nightly(1, { fixed: 1 })] }, attachTo: document.body })
    await settle()
    expect(text(wrapper.find('[data-slot=chart-legend]').element)).toBe('New failure Fixed')
    wrapper.unmount()
  })

  it('shows an empty state when no retained nightly moved', () => {
    const wrapper = mount(NightlyMovementChart, { props: { builds: [nightly(2, {}), nightly(1, {})], emptyDescription: 'Latest signal #2' } })
    expect(wrapper.find('[data-slot=bar-chart]').exists()).toBe(false)
    expect(text(wrapper.find('[data-slot=empty-state]').element)).toBe('No failure movement in the retained nightlies Latest signal #2')
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

  it('turns segments and legend entries into buttons when selectable and mutes zero counts', async () => {
    const picked: string[] = []
    const wrapper = mount(ProportionBar, { props: { segments, label: 'Groups', max: 16, onSelect: (s: { key: string }) => picked.push(s.key) } })
    const bar = wrapper.find('[role=group]')
    expect(bar.attributes('aria-label')).toBe('Groups: Passing 6, Hard 2, Soft 0')
    const parts = bar.findAll('button')
    expect(parts.map((p) => p.attributes('aria-label'))).toEqual(['Passing: 6', 'Hard: 2'])
    expect(parts[0]!.attributes('style')).toContain('width: 37.5%')
    await parts[0]!.trigger('click')
    const buttons = wrapper.findAll('li button')
    await buttons[1]!.trigger('click')
    expect(picked).toEqual(['pass', 'hard'])
    expect(buttons[2]!.find('.tabular-nums').classes()).toContain('text-muted-foreground')
  })
})

describe('useDragRange', () => {
  // A plot from x=100 to x=300 on screen mapping linearly onto 0..1000.
  async function setup() {
    const ranges: number[][] = []
    const clicks: string[] = []
    let drag!: ReturnType<typeof useDragRange>
    const Host = defineComponent({
      setup() {
        drag = useDragRange({
          toPlot: (x) => ({ local: Math.max(0, Math.min(200, x - 100)), hostPx: Math.max(0, Math.min(200, x - 100)) }),
          invert: (local) => local * 5,
          onRange: () => (a, b) => ranges.push([a, b]),
        })
        return () => h('div', { onMousedown: drag.onDown, onClickCapture: drag.onClickCapture }, [h('span', { onClick: () => clicks.push('mark') })])
      },
    })
    const wrapper = mount(Host, { attachTo: document.body })
    // Vue ignores events stamped in the same millisecond its listeners were attached.
    await new Promise((resolve) => setTimeout(resolve, 2))
    const mouse = (type: string, clientX: number) => new MouseEvent(type, { bubbles: true, button: 0, clientX })
    return { wrapper, drag, ranges, clicks, mouse }
  }

  it('reports the dragged range, shows the selection, and swallows the trailing click', async () => {
    const { wrapper, drag, ranges, clicks, mouse } = await setup()
    const mark = wrapper.find('span').element
    mark.dispatchEvent(mouse('mousedown', 250))
    window.dispatchEvent(mouse('mousemove', 140))
    expect(drag.selection.value).toEqual({ left: 40, width: 110 })
    window.dispatchEvent(mouse('mouseup', 140))
    mark.dispatchEvent(mouse('click', 140))
    expect(ranges).toEqual([[200, 750]])
    expect(clicks).toEqual([])
    expect(drag.selection.value).toBeNull()
    wrapper.unmount()
  })

  it('lets a plain click through to the marks', async () => {
    const { wrapper, ranges, clicks, mouse } = await setup()
    const mark = wrapper.find('span').element
    mark.dispatchEvent(mouse('mousedown', 150))
    window.dispatchEvent(mouse('mousemove', 152))
    window.dispatchEvent(mouse('mouseup', 152))
    mark.dispatchEvent(mouse('click', 152))
    expect(ranges).toEqual([])
    expect(clicks).toEqual(['mark'])
    wrapper.unmount()
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

describe('barValueTicks', () => {
  const MOVEMENT = [
    { key: 'new', label: 'New', color: statusVar('hard') },
    { key: 'fixed', label: 'Fixed', color: statusVar('pass') },
  ]

  it('shares one scale around zero when diverging bars sit on both sides', () => {
    expect(barValueTicks([{ new: 12, fixed: 3 }], MOVEMENT, { diverging: ['fixed'] })).toEqual([-20, -10, 0, 10, 20])
  })

  it('keeps a one-sided diverging chart on a one-sided axis', () => {
    expect(barValueTicks([{ new: 12, fixed: 0 }], MOVEMENT, { diverging: ['fixed'] })).toEqual([0, 5, 10, 15])
    expect(barValueTicks([{ new: 0, fixed: 7 }], MOVEMENT, { diverging: ['fixed'] })).toEqual([-8, -6, -4, -2, 0])
  })

  it('sums stacks, takes the longest bar when grouped, and halves the ticks when compact', () => {
    const rows = [{ new: 6, fixed: 5 }]
    const series = MOVEMENT.map((s) => ({ ...s }))
    expect(barValueTicks(rows, series)).toEqual([0, 5, 10, 15])
    expect(barValueTicks(rows, series, { stacked: false })).toEqual([0, 2, 4, 6])
    expect(barValueTicks([{ new: 12, fixed: 3 }], MOVEMENT, { diverging: ['fixed'], compact: true })).toEqual([-20, 0, 20])
  })
})

describe('niceTicks', () => {
  it('ends on a round tick at or beyond the largest value', () => {
    expect(niceTicks(0, 188, 4)).toEqual([0, 50, 100, 150, 200])
    expect(niceTicks(0, 14, 4)).toEqual([0, 5, 10, 15])
    expect(niceTicks(0, 150, 4)).toEqual([0, 50, 100, 150])
    expect(niceTicks(-7, 12, 4)).toEqual([-10, -5, 0, 5, 10, 15])
  })

  it('never steps finer than the minimum step, and spans an empty range', () => {
    expect(niceTicks(0, 2, 4, 1)).toEqual([0, 1, 2])
    expect(niceTicks(0, 1, 4)).toEqual([0, 0.25, 0.5, 0.75, 1])
    expect(niceTicks(0, 0, 4, 1)).toEqual([0, 1])
  })
})

describe('BarChart interaction', () => {
  it('gives clickable bars a pointer and keeps a legend when one series encodes a state', () => {
    const clickable = mount(BarChart<Row>, {
      props: { rows, category: (r: Row) => shortDay(r.day), series: [failures[0]!], label: 'Failures', legend: true, onSelect: () => {} },
    })
    const plot = clickable.find('[role=img]')
    expect(plot.attributes('data-clickable')).toBe('')
    expect(plot.classes()).toContain('[--vis-stacked-bar-cursor:pointer]')
    expect(clickable.find('[data-slot=chart-legend]').text()).toContain('Hard')
    const still = mount(BarChart<Row>, { props: { rows, category: (r: Row) => shortDay(r.day), series: [failures[0]!], label: 'Failures' } })
    expect(still.find('[role=img]').attributes('data-clickable')).toBeUndefined()
    expect(still.find('[data-slot=chart-legend]').exists()).toBe(false)
  })
})

describe('Heatmap', () => {
  const columns = ['2026-10-10', '2026-10-11', '2026-10-12'].map((day, i) => ({ id: day, label: String(10 + i), title: `Oct ${10 + i}` }))
  const row = (id: string, values: number[], marker = -1) => ({
    id,
    label: id,
    dot: 'var(--hw-mi355)' as never,
    cells: values.map((value, i) => ({ value, marker: i === marker })),
    total: values.reduce((a, b) => a + b, 0),
  })
  const rows = [row('node-a', [0, 4, 1], 1), row('node-b', [2, 0, 0])]
  const summary = { id: 'fleet', label: 'All nodes', cells: [2, 4, 1].map((value) => ({ value })), total: 7 }

  it('colors cells by heat step, with the summary on its own scale', () => {
    const wrapper = mount(Heatmap, { props: { columns, rows, summary, label: 'Failures' } })
    const color = (el: { attributes: (name: string) => string | undefined }) => /background-color: ([^;]+)/.exec(el.attributes('style') ?? '')?.[1]
    const cells = wrapper.findAll('[data-slot=heatmap-cell]')
    expect(cells).toHaveLength(9)
    // Summary row (max 4), then node rows (max 4).
    expect(cells.map(color)).toEqual([3, 5, 2, 0, 5, 2, 3, 0, 0].map((level) => `var(--heat-${level})`))
    expect(wrapper.findAll('[data-slot=heatmap-marker]')).toHaveLength(1)
    expect(text(wrapper.find('[data-slot=heatmap-legend]').element)).toContain('max 4 per cell; All nodes on its own scale, max 4')
    // Without handlers nothing is a button.
    expect(wrapper.find('button').exists()).toBe(false)
  })

  it('makes non-empty cells, row labels and day headers buttons that report what they open', async () => {
    const onCell = vi.fn()
    const onRow = vi.fn()
    const onColumn = vi.fn()
    const wrapper = mount(Heatmap, { props: { columns, rows, summary, label: 'Failures', markerLabel: 'Co-failure event', cellAction: 'open the runs', onCell, onRow, onColumn }, attachTo: document.body })
    const cells = wrapper.findAll('button[data-slot=heatmap-cell]')
    // Empty cells stay plain and hidden from assistive tech; the summary row is clickable too.
    expect(cells).toHaveLength(6)
    expect(cells[3]!.attributes('aria-label')).toBe('node-a · Oct 11: 4 failures, Co-failure event, open the runs')
    await cells[3]!.trigger('click')
    expect(onCell).toHaveBeenCalledWith(rows[0], 1)
    await cells[3]!.trigger('mouseenter')
    const tip = wrapper.find('[data-slot=heatmap-tooltip]')
    expect(text(tip.element)).toContain('node-a · Oct 11')
    expect(text(tip.element)).toContain('Click to open the runs')
    await cells[3]!.trigger('mouseleave')
    expect(wrapper.find('[data-slot=heatmap-tooltip]').exists()).toBe(false)
    const labels = wrapper.findAll('button[data-slot=heatmap-row]')
    expect(labels.map((b) => b.attributes('aria-label'))).toEqual(['node-a: 5 failures, open the row', 'node-b: 2 failures, open the row'])
    await labels[1]!.trigger('click')
    expect(onRow).toHaveBeenCalledWith(rows[1])
    expect(wrapper.find('[data-slot=heatmap-summary]').element.tagName).toBe('SPAN')
    const headers = wrapper.findAll('button[data-slot=heatmap-column]')
    expect(headers.map((b) => b.attributes('aria-label'))).toEqual(['Oct 10: open the day', 'Oct 11: open the day', 'Oct 12: open the day'])
    await headers[2]!.trigger('click')
    expect(onColumn).toHaveBeenCalledWith(2)
    wrapper.unmount()
  })

  it('keeps one cell in the tab order and moves between failing cells with the arrow keys', async () => {
    const wrapper = mount(Heatmap, { props: { columns, rows, summary, label: 'Failures', onCell: () => {} }, attachTo: document.body })
    const stops = () => wrapper.findAll('button[data-slot=heatmap-cell]').filter((b) => b.attributes('tabindex') === '0').map((b) => b.attributes('data-spot'))
    expect(stops()).toEqual(['0:0'])
    const at = (spot: string) => wrapper.find(`[data-spot="${spot}"]`)
    await at('0:0').trigger('focus')
    await at('0:0').trigger('keydown', { key: 'End' })
    expect(document.activeElement).toBe(at('0:2').element)
    // Down from the summary's last day lands on node-a's nearest failing day; empty cells are skipped.
    await at('0:2').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(at('1:2').element)
    await at('1:2').trigger('focus')
    await at('1:2').trigger('keydown', { key: 'ArrowDown' })
    expect(document.activeElement).toBe(at('2:0').element)
    await at('2:0').trigger('focus')
    expect(stops()).toEqual(['2:0'])
    wrapper.unmount()
  })

  it('names its rows with the given action and unit', () => {
    const wrapper = mount(Heatmap, { props: { columns, rows: [row('n', [1, 0, 0])], label: 'Runs', unit: ['run', 'runs'] as [string, string], rowAction: 'open the node', onRow: () => {} } })
    expect(wrapper.find('button[data-slot=heatmap-row]').attributes('aria-label')).toBe('n: 1 run, open the node')
  })
})
