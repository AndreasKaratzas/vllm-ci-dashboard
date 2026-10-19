import { afterEach, describe, expect, it } from 'vitest'
import { mount } from '@vue/test-utils'
import { nextTick } from 'vue'
import OmniDetailSheet from '@/components/omni/OmniDetailSheet.vue'
import { OMNI_EMPTY_TABLE_TEXT, type OmniDetail } from '@/domain/omni'

const txt = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()

function detailWith(rows: Record<string, unknown>[]): OmniDetail {
  return {
    id: 'buckets',
    title: 'Buckets',
    fields: [],
    sources: [],
    tables: [{ title: 'UTC buckets', columns: [{ label: 'Bucket', cell: (r: Record<string, unknown>) => ({ text: String(r.bucket) }) }], rows }],
  } as unknown as OmniDetail
}

async function render(detail: OmniDetail) {
  const wrapper = mount(OmniDetailSheet, { props: { detail, open: true }, attachTo: document.body })
  await nextTick()
  await nextTick()
  return wrapper
}

afterEach(() => {
  document.body.innerHTML = ''
})

describe('OmniDetailSheet', () => {
  it('draws a table with no rows as the classic empty note instead of an empty table', async () => {
    const wrapper = await render(detailWith([]))
    const sheet = document.querySelector('[data-testid=omni-detail]')
    expect(sheet).not.toBeNull()
    expect(sheet!.querySelector('table')).toBeNull()
    expect(txt(sheet!.querySelector('[data-testid=omni-detail-table-empty]'))).toBe(OMNI_EMPTY_TABLE_TEXT)
    wrapper.unmount()
  })

  it('draws a table with rows as a table', async () => {
    const wrapper = await render(detailWith([{ bucket: '00:00' }]))
    const sheet = document.querySelector('[data-testid=omni-detail]')
    expect(sheet!.querySelector('[data-testid=omni-detail-table-empty]')).toBeNull()
    expect(txt(sheet!.querySelector('tbody'))).toContain('00:00')
    wrapper.unmount()
  })
})
