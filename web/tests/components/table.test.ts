import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import Cell from '@/components/table/Cell.vue'
import DataTable from '@/components/table/DataTable.vue'
import { badge, drill, link, mono, num, text as textCell, type ColumnSpec } from '@/components/table/columns'
import { installLayoutStubs, settle, text } from './support'

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

type Group = { name: string; runs: number; state: 'pass' | 'hard'; url: string }
const groups: Group[] = Array.from({ length: 12 }, (_, i) => ({
  name: `group-${String.fromCharCode(97 + i)}`,
  runs: (i * 7) % 12,
  state: i % 3 ? 'pass' : 'hard',
  url: `https://buildkite.com/b/${i}`,
}))
const columns: ColumnSpec<Group>[] = [
  { id: 'name', label: 'Group', value: (g) => g.name, cell: (g) => link(g.name, g.url) },
  { id: 'runs', label: 'Runs', value: (g) => g.runs, numeric: true },
  { id: 'state', label: 'State', value: (g) => g.state, cell: (g) => badge(g.state === 'pass' ? 'Passing' : 'Failing', g.state) },
]
const firstColumn = (wrapper: ReturnType<typeof mount>) => wrapper.findAll('tbody tr').map((tr) => text(tr.find('td').element))

describe('DataTable', () => {
  it('pages rows and reports the count', async () => {
    const wrapper = mount(DataTable<Group>, { props: { columns, rows: groups }, attachTo: document.body })
    expect(wrapper.findAll('tbody tr')).toHaveLength(10)
    expect(wrapper.text()).toContain('12 rows')
    expect(wrapper.text()).toContain('Page 1 of 2')
    await wrapper.find('button[aria-label="Next page"]').trigger('click')
    expect(firstColumn(wrapper)).toEqual(['group-k', 'group-l'])
    expect(wrapper.find('button[aria-label="Next page"]').attributes('disabled')).toBeDefined()
    wrapper.unmount()
  })

  it('returns to the first page when the search, rows or filters change', async () => {
    const wrapper = mount(DataTable<Group>, {
      props: {
        columns,
        rows: groups,
        pageSize: 5,
        filters: [{ id: 'state', label: 'State', options: [{ value: 'pass', label: 'Passing' }], predicate: (g: Group, v: string) => g.state === v }],
      },
      attachTo: document.body,
    })
    const next = () => wrapper.find('button[aria-label="Next page"]').trigger('click')
    await next()
    await wrapper.find('input').setValue('group')
    expect(wrapper.text()).toContain('Page 1 of 3')
    await next()
    await wrapper.setProps({ rows: [...groups] })
    expect(wrapper.text()).toContain('Page 1 of 3')
    await next()
    wrapper.find('[role=combobox]').element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, ctrlKey: false, pointerType: 'mouse' }))
    await settle()
    ;[...document.querySelectorAll<HTMLElement>('[role=option]')].find((o) => text(o) === 'Passing')!.dispatchEvent(new PointerEvent('pointerup', { bubbles: true, pointerType: 'mouse' }))
    await settle()
    expect(wrapper.text()).toContain('8 rows')
    expect(wrapper.text()).toContain('Page 1 of 2')
    wrapper.unmount()
  })

  it('hides columns from the Columns menu but never the first one', async () => {
    const wrapper = mount(DataTable<Group>, { props: { columns, rows: groups.slice(0, 2) }, attachTo: document.body })
    const menu = wrapper.findAll('button').find((b) => b.text() === 'Columns')!
    menu.element.dispatchEvent(new PointerEvent('pointerdown', { bubbles: true, button: 0, ctrlKey: false, pointerType: 'mouse' }))
    menu.element.dispatchEvent(new KeyboardEvent('keydown', { key: 'Enter', bubbles: true }))
    await settle()
    const items = [...document.querySelectorAll<HTMLElement>('[role=menuitemcheckbox]')]
    expect(items.map((item) => text(item))).toEqual(['Runs', 'State'])
    items[0]!.click()
    await settle()
    expect(wrapper.findAll('th').map((th) => text(th.element))).toEqual(['Group', 'State'])
    expect(wrapper.find('tbody tr').findAll('td')).toHaveLength(2)
    wrapper.unmount()
  })

  it('sorts numerically through header buttons and exposes aria-sort', async () => {
    const wrapper = mount(DataTable<Group>, { props: { columns, rows: groups, pageSize: false } })
    const runs = wrapper.findAll('th')[1]!
    expect(runs.attributes('aria-sort')).toBe('none')
    await runs.find('button').trigger('click')
    expect(runs.attributes('aria-sort')).toBe('ascending')
    expect(wrapper.findAll('tbody tr').map((tr) => text(tr.findAll('td')[1]!.element)).slice(0, 3)).toEqual(['0', '1', '2'])
    await runs.find('button').trigger('click')
    expect(runs.attributes('aria-sort')).toBe('descending')
    expect(text(wrapper.find('tbody tr').findAll('td')[1]!.element)).toBe('11')
    expect(runs.find('button').classes()).toContain('hover:text-foreground')
  })

  it('searches column values plus extra search text, and filters', async () => {
    const wrapper = mount(DataTable<Group>, {
      props: {
        columns,
        rows: groups,
        searchText: (g: Group) => (g.name === 'group-c' ? 'flaky kernel' : ''),
        filters: [{ id: 'state', label: 'State', options: [{ value: 'hard', label: 'Failing' }], initial: 'hard', predicate: (g: Group, v: string) => g.state === v }],
      },
    })
    expect(firstColumn(wrapper)).toEqual(['group-a', 'group-d', 'group-g', 'group-j'])
    await wrapper.find('input').setValue('kernel')
    expect(text(wrapper.find('tbody').element)).toBe('No rows.')
    await wrapper.setProps({ filters: [] })
    expect(firstColumn(wrapper)).toEqual(['group-c'])
  })

  it('makes rows clickable with keyboard, cursor, hover and a chevron', async () => {
    const opened: string[] = []
    const wrapper = mount(DataTable<Group>, { props: { columns, rows: groups.slice(0, 2), onRowClick: (g: Group) => opened.push(g.name) } })
    const row = wrapper.find('tbody tr')
    expect(row.attributes('tabindex')).toBe('0')
    expect(row.classes()).toEqual(expect.arrayContaining(['cursor-pointer', 'group/row']))
    expect(row.find('td:last-child svg').classes()).toContain('group-hover/row:opacity-100')
    await row.trigger('click')
    await row.trigger('keydown', { key: 'Enter' })
    expect(opened).toEqual(['group-a', 'group-a'])
    await row.find('a').trigger('click')
    expect(opened).toHaveLength(2)
  })

  it('leaves rows inert without a handler', () => {
    const row = mount(DataTable<Group>, { props: { columns, rows: groups.slice(0, 1) } }).find('tbody tr')
    expect(row.attributes('tabindex')).toBeUndefined()
    expect(row.classes()).not.toContain('cursor-pointer')
  })

  it('can drop the toolbar and pager for compact tables', () => {
    const wrapper = mount(DataTable<Group>, { props: { columns: columns.map((c) => ({ ...c, hideable: false })), rows: groups, searchable: false, pageSize: false } })
    expect(wrapper.find('input').exists()).toBe(false)
    expect(wrapper.findAll('tbody tr')).toHaveLength(12)
    expect(wrapper.text()).not.toContain('Page')
  })
})

describe('Cell', () => {
  it('renders external links with an icon and a new tab', () => {
    const a = mount(Cell, { props: { cell: link('#123', 'https://buildkite.com/x', true) } }).find('a')
    expect(a.attributes('target')).toBe('_blank')
    expect(a.classes()).toContain('font-mono')
    expect(a.find('svg').exists()).toBe(true)
  })

  it('renders status as icon plus label and drills into evidence', async () => {
    const detail = { id: 'd', title: 'Group a' }
    const wrapper = mount(Cell, { props: { cell: { ...drill('Failing', () => detail), tone: 'hard' as const } } })
    expect(wrapper.find('button svg').exists()).toBe(true)
    expect(text(wrapper.element)).toBe('Failing')
    await wrapper.find('button').trigger('click')
    expect(wrapper.emitted('drill')).toEqual([[detail]])
  })

  it('renders missing values as a muted dash and keeps secondary text', () => {
    expect(mount(Cell, { props: { cell: num(null) } }).find('span span').classes()).toContain('text-muted-foreground')
    expect(text(mount(Cell, { props: { cell: textCell('main', 'since #12') } }).element)).toBe('main since #12')
    expect(mount(Cell, { props: { cell: mono('abc1234') } }).find('.font-mono').text()).toBe('abc1234')
  })
})
