import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { defineComponent, h } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import EvidenceSheet from '@/components/evidence/EvidenceSheet.vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import { drill, text as textCell } from '@/components/table/columns'
import { useSheetStack } from '@/composables/useSheetStack'
import { installLayoutStubs, settle, text } from './support'

installLayoutStubs()
afterEach(() => {
  document.body.innerHTML = ''
})

const child: Detail = { id: 'job', title: 'Job 7', subtitle: 'kernels shard 2', width: 'md' }
const parent: Detail = {
  id: 'build',
  title: 'Nightly #12',
  subtitle: 'Build evidence',
  description: 'Every retained job.',
  notes: [{ tone: 'soft', title: 'Partial', text: 'Two shards missing.' }],
  stats: [
    { label: 'Hard', value: '3', tone: 'hard' },
    { label: 'Passed', value: '120', tone: 'pass' },
  ],
  fields: [
    { label: 'Commit', value: 'abc1234' },
    { label: 'Empty', value: '' },
  ],
  sources: [{ label: 'Buildkite', url: 'https://buildkite.com/b/12' }],
  tables: [{ title: 'Jobs', meta: '1 job', columns: [{ id: 'job', label: 'Job', value: (r: { n: string }) => r.n, cell: (r: { n: string }) => drill(r.n, () => child) }], rows: [{ n: 'Job 7' }] }],
  width: 'xl',
}

const sheets = () => [...document.querySelectorAll('[data-slot=evidence-sheet]')]

describe('EvidenceSheet', () => {
  it('renders every part of a Detail', async () => {
    const wrapper = mount(EvidenceSheet, { props: { detail: parent, open: true }, attachTo: document.body })
    await settle()
    const sheet = sheets()[0]!
    expect(sheet.className).toContain('sm:max-w-5xl')
    expect(text(sheet.querySelector('h2'))).toBe('Nightly #12')
    expect(text(sheet)).toContain('Every retained job.')
    expect(text(sheet.querySelector('[role=status]'))).toBe('Partial Two shards missing.')
    expect(text(sheet.querySelector('dl'))).toBe('Hard 3 Hard fail Passed 120')
    expect(text(sheet.querySelectorAll('dl')[1]!)).toBe('Commit abc1234')
    const source = sheet.querySelector<HTMLAnchorElement>('a[href="https://buildkite.com/b/12"]')!
    expect(source.target).toBe('_blank')
    expect(text(sheet.querySelector('section'))).toContain('Jobs 1 job')
    expect(sheet.querySelector('button.-ml-2')).toBeNull()
    wrapper.unmount()
  })
})

describe('SheetStack', () => {
  it('pushes drill-downs and goes Back to the previous sheet', async () => {
    const Host = defineComponent({
      setup() {
        const stack = useSheetStack()
        stack.open(parent)
        return () => h(SheetStack, { sheets: stack })
      },
    })
    const wrapper = mount(Host, { attachTo: document.body })
    await settle()
    expect(sheets()).toHaveLength(1)
    sheets()[0]!.querySelector<HTMLButtonElement>('tbody button')!.click()
    await settle()
    expect(sheets().map((s) => s.getAttribute('data-detail'))).toEqual(['build', 'job'])
    const back = [...sheets()[1]!.querySelectorAll('button')].find((b) => text(b) === 'Back')!
    back.click()
    await settle()
    await new Promise((resolve) => setTimeout(resolve, 50))
    expect(sheets().map((s) => s.getAttribute('data-detail'))).toEqual(['build'])
    wrapper.unmount()
  })
})

describe('useSheetStack', () => {
  it('opens, pushes and closes from an index', () => {
    const stack = useSheetStack()
    stack.open(parent)
    stack.push(child)
    expect(stack.stack.value.map((d) => d.id)).toEqual(['build', 'job'])
    stack.closeFrom(1)
    expect(stack.stack.value.map((d) => d.id)).toEqual(['build'])
    stack.open(child)
    expect(stack.stack.value.map((d) => d.id)).toEqual(['job'])
    stack.close()
    expect(stack.stack.value).toEqual([])
    expect(textCell(null).text).toBe('—')
  })
})
