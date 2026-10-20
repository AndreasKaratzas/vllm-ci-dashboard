import { describe, expect, it } from 'vitest'
import { OMNI_EMPTY_TABLE_TEXT, type DetailTable, type OmniDetail } from '@/domain/omni'
import { SERIES } from '@/lib/palette'
import { collectionGap, firstSentence, kpiValue, rowDetail, sentenceCase, toColumns, toDelta, toDetail, WITHHELD_HINT } from '@/views/omni/present'

type Row = Record<string, unknown>

function detailWith(tables: DetailTable<Row>[], open?: () => OmniDetail, description?: string): OmniDetail {
  return {
    id: 'buckets',
    title: '',
    subtitle: '',
    description,
    fields: [
      { label: 'Kept', value: 0 },
      { label: 'Dropped', value: null },
      { label: 'Share', value: 'Unavailable' },
      { label: 'Passed', value: '-' },
    ],
    sources: [],
    strip: [{ label: 'GPU-SLOT REQUESTS', value: '4,286', meta: 'summed widths' }],
    tables: tables.map((table) => ({ ...table, columns: table.columns.map((c) => ({ ...c, cell: (r: Row) => ({ ...c.cell(r), open }) })) })),
  }
}
const bucketTable = (rows: Row[], title = 'UTC buckets'): DetailTable<Row> => ({ title, columns: [{ label: 'Bucket', cell: (r) => ({ text: String(r.bucket) }) }], rows })

describe('Omni evidence sheets', () => {
  it('draws a sheet whose tables are all empty as one empty note', () => {
    const detail = toDetail(detailWith([bucketTable([])]))
    expect(detail.tables).toEqual([])
    expect(detail.empty).toBe(OMNI_EMPTY_TABLE_TEXT)
    expect([detail.title, detail.subtitle]).toEqual(['Evidence', 'Source evidence and retained history'])
  })

  it('keeps an empty table with its title next to a table with rows', () => {
    const detail = toDetail(detailWith([bucketTable([{ bucket: '00:00' }]), bucketTable([], 'Pipelines')]))
    expect(detail.tables!.map((t) => [t.title, t.rows.length, t.emptyText])).toEqual([
      ['UTC buckets', 1, OMNI_EMPTY_TABLE_TEXT],
      ['Pipelines', 0, OMNI_EMPTY_TABLE_TEXT],
    ])
    expect(detail.empty).toBeUndefined()
  })

  it('keeps drill-downs on cells and whole rows, fields, and the stat strip', () => {
    const nested = detailWith([bucketTable([{ bucket: 'nested' }])])
    const detail = toDetail(detailWith([bucketTable([{ bucket: '00:00' }])], () => nested))
    const table = detail.tables![0]!
    const cell = table.columns[0]!.cell!({ bucket: '00:00' })
    expect(cell.text).toBe('00:00')
    expect(cell.detail!().tables![0]!.rows).toEqual([{ bucket: 'nested' }])
    expect(table.rowDetail!({ bucket: '00:00' })!.tables![0]!.rows).toEqual([{ bucket: 'nested' }])
    expect(detail.fields).toEqual([
      { label: 'Kept', value: '0', hint: undefined },
      { label: 'Dropped', value: null },
      { label: 'Share', value: '—', hint: WITHHELD_HINT },
      { label: 'Passed', value: '—', hint: undefined },
    ])
    expect(detail.stats).toEqual([{ label: 'GPU-slot requests', value: '4,286', meta: 'summed widths' }])
  })

  it('moves a long methodology description below the data and keeps a short one on top', () => {
    const long = 'x'.repeat(200)
    expect(toDetail(detailWith([], undefined, long))).toMatchObject({ description: undefined, footnote: long })
    expect(toDetail(detailWith([], undefined, 'Short.'))).toMatchObject({ description: 'Short.', footnote: undefined })
  })
})

describe('Omni table columns', () => {
  const url = 'https://buildkite.com/job/1'
  const jobs: DetailTable<Row> = {
    rows: [
      { name: 'a', state: 'running', analysis: 'active', age: '1m', url },
      { name: 'b', state: 'waiting', analysis: 'active', age: '2m', url: url + '2' },
    ],
    columns: [
      { label: 'Job', cell: (r) => ({ text: String(r.name), href: String(r.url) }) },
      { label: 'State', cell: (r) => ({ text: String(r.state), tone: 'neutral', href: String(r.url) }) },
      { label: 'Age', numeric: true, cell: (r) => ({ text: String(r.age), href: String(r.url) }) },
      { label: 'Analysis', cell: (r) => ({ text: String(r.analysis), tone: 'pass' }) },
      { label: 'Share', numeric: true, cell: () => ({ text: 'Unavailable' }) },
    ],
  }

  it('drops a badge column that reads the same on every row and links only the first cell', () => {
    const columns = toColumns(jobs, { minWidth: { Age: 'sm' } })
    expect(columns.map((c) => [c.label, c.minWidth])).toEqual([
      ['Job', undefined],
      ['State', undefined],
      ['Age', 'sm'],
      ['Share', undefined],
    ])
    const row = jobs.rows[0]!
    expect(columns.map((c) => c.cell!(row).href)).toEqual([url, undefined, undefined, undefined])
    expect(columns[3]!.cell!(row)).toMatchObject({ text: '—', title: WITHHELD_HINT })
  })

  it('keeps every column of a single row, where nothing repeats', () => {
    expect(toColumns({ ...jobs, rows: jobs.rows.slice(0, 1) }).map((c) => c.label)).toEqual(['Job', 'State', 'Age', 'Analysis', 'Share'])
  })

  it('opens whole rows instead of cells and draws one magnitude bar', () => {
    const open = () => detailWith([])
    const queues: DetailTable<Row> = {
      rows: [
        { name: 'amd_mi300_1', mapped: 30 },
        { name: 'amd_mi325_1', mapped: 10 },
      ],
      columns: [
        { label: 'Queue', cell: (r) => ({ text: String(r.name), open }) },
        { label: 'Mapped', numeric: true, cell: (r) => ({ text: String(r.mapped), open }) },
      ],
    }
    const columns = toColumns(queues, { rowClick: true, mono: ['Queue'], bar: { label: 'Mapped', value: (r) => Number(r.mapped), color: () => SERIES.primary } })
    const cells = columns.map((c) => c.cell!(queues.rows[1]!))
    expect(cells[0]).toMatchObject({ text: 'amd_mi325_1', mono: true, detail: undefined })
    expect(cells[1]).toMatchObject({ text: '10', detail: undefined, bar: { value: 10, max: 30, color: SERIES.primary } })
    expect(rowDetail(queues)!(queues.rows[0]!)!.id).toBe('buckets')
    expect(rowDetail({ ...queues, columns: [{ label: 'Queue', cell: (r: Row) => ({ text: String(r.name) }) }] })).toBeUndefined()
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

  it('cuts a coverage reason to its first sentence', () => {
    expect(firstSentence('The current UTC hour is open through Oct 19, 07:44 AM. Rates are unavailable.')).toBe('The current UTC hour is open through Oct 19, 07:44 AM.')
  })

  it('labels a change as relative for counts and in points for shares, naming its baseline', () => {
    expect(toDelta(null)).toBeUndefined()
    expect(toDelta({ kind: 'percent', value: 51.4 })).toEqual({ value: 51.4, text: '+51%', label: 'vs the first half of the window' })
    expect(toDelta({ kind: 'points', value: -2 })).toMatchObject({ value: -2, text: '-2.0 pp' })
  })

  it('calls spacing beyond a multiple of the median step a collection gap', () => {
    const hour = 3_600_000
    expect(collectionGap([0, hour, 2 * hour, 9 * hour], 1.5)).toBe(1.5 * hour)
    expect(collectionGap([5], 3)).toBeUndefined()
  })
})
