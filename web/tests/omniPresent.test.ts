import { describe, expect, it } from 'vitest'
import { OMNI_EMPTY_TABLE_TEXT, type OmniDetail } from '@/domain/omni'
import { firstSentence, kpiValue, sentenceCase, toDetail } from '@/views/omni/present'

function detailWith(rows: Record<string, unknown>[], open?: () => OmniDetail): OmniDetail {
  return {
    id: 'buckets',
    title: '',
    subtitle: '',
    fields: [{ label: 'Kept', value: 0 }, { label: 'Dropped', value: null }],
    sources: [],
    strip: [{ label: 'GPU-SLOT REQUESTS', value: '4,286', meta: 'summed widths' }],
    tables: [{ title: 'UTC buckets', columns: [{ label: 'Bucket', cell: (r: Record<string, unknown>) => ({ text: String(r.bucket), open }) }], rows }],
  }
}

describe('Omni evidence sheets', () => {
  it('draws a table with no rows as the empty note instead of an empty table', () => {
    const detail = toDetail(detailWith([]))
    expect(detail.tables).toEqual([])
    expect(detail.empty).toBe(OMNI_EMPTY_TABLE_TEXT)
    expect([detail.title, detail.subtitle]).toEqual(['Evidence', 'Source evidence and retained history'])
  })

  it('keeps tables with rows, their drill-downs, fields and the stat strip', () => {
    const nested = detailWith([{ bucket: 'nested' }])
    const detail = toDetail(detailWith([{ bucket: '00:00' }], () => nested))
    expect(detail.empty).toBeUndefined()
    const cell = detail.tables![0]!.columns[0]!.cell!({ bucket: '00:00' })
    expect(cell.text).toBe('00:00')
    expect(cell.detail!().tables![0]!.rows).toEqual([{ bucket: 'nested' }])
    expect(detail.fields).toEqual([{ label: 'Kept', value: '0' }, { label: 'Dropped', value: null }])
    expect(detail.stats).toEqual([{ label: 'GPU-slot requests', value: '4,286', meta: 'summed widths' }])
  })
})

describe('Omni presentation helpers', () => {
  it('writes the classic upper-case labels in sentence case, keeping product names', () => {
    expect(sentenceCase('SHARE OF OBSERVED VLLM TRAFFIC')).toBe('Share of observed vLLM traffic')
    expect(sentenceCase('MI325 RETIREMENT EXPOSURE')).toBe('MI325 retirement exposure')
    expect(sentenceCase('OBSERVED OMNI MAPPINGS')).toBe('Observed Omni mappings')
    expect(sentenceCase('GPU-HOURS')).toBe('GPU-hours')
  })

  it('shows missing values as empty and plain counts as numbers', () => {
    expect([kpiValue('-'), kpiValue('Unavailable'), kpiValue('1,882'), kpiValue('≥3,476'), kpiValue('22.7%')]).toEqual([null, null, 1882, '≥3,476', '22.7%'])
  })

  it('cuts the coverage note to its first sentence', () => {
    expect(firstSentence('The current UTC hour is open through Oct 19, 07:44 AM. Rates are unavailable.')).toBe(
      'The current UTC hour is open through Oct 19, 07:44 AM.',
    )
  })
})
