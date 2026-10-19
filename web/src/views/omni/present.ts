import type { Detail, DetailTable as SheetTable } from '@/components/evidence/detail'
import type { CellSpec, ColumnSpec } from '@/components/table/columns'
import type { Rec } from '@/domain/format'
import { OMNI_EMPTY_TABLE_TEXT, detailSubtitle, detailTitle, type Cell, type DetailTable, type OmniDetail } from '@/domain/omni'

const ACRONYMS: [RegExp, string][] = [
  [/\bgpu\b/g, 'GPU'],
  [/\bmi(\d+)/g, 'MI$1'],
  [/\bvllm\b/g, 'vLLM'],
  [/\bomni\b/g, 'Omni'],
  [/\bapi\b/g, 'API'],
  [/\buuid\b/g, 'UUID'],
]

/** 'SHARE OF OBSERVED VLLM TRAFFIC' -> 'Share of observed vLLM traffic'. */
export function sentenceCase(label: string): string {
  let text = label.trim().toLowerCase()
  for (const [pattern, replacement] of ACRONYMS) text = text.replace(pattern, replacement)
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** Domain values that mean "no value" ('-', 'Unavailable') render as a muted em dash. */
export function missing(value: string): boolean {
  return value === '-' || value === 'Unavailable' || value === ''
}

export function toCell(cell: Cell): CellSpec {
  const open = cell.open
  return {
    text: cell.text === '-' ? '—' : cell.text,
    href: cell.href,
    tone: cell.tone,
    mono: cell.mono,
    detail: open ? () => toDetail(open()) : undefined,
  }
}

const sortKey = (text: string, numeric?: boolean): string | number => {
  if (!numeric) return text
  const parsed = Number(text.replace(/[,%≥+]/g, ''))
  return Number.isFinite(parsed) ? parsed : -Infinity
}

/** `compact` tables show every column (no column picker), as page previews do. */
export function toColumns<T>(table: DetailTable<T>, compact = false): ColumnSpec<T>[] {
  return table.columns.map((column, index) => ({
    id: `${index}-${column.label}`,
    label: column.label,
    numeric: column.numeric,
    hideable: compact ? false : undefined,
    value: (row: T) => sortKey(column.cell(row).text, column.numeric),
    cell: (row: T) => toCell(column.cell(row)),
  }))
}

function toTable(table: DetailTable<Rec>): SheetTable<Rec> {
  return {
    title: table.title,
    meta: table.meta,
    columns: toColumns(table, table.columns.length <= 5),
    rows: table.rows,
    searchText: table.searchText,
    emptyText: OMNI_EMPTY_TABLE_TEXT,
  }
}

/** An Omni drawer as an evidence sheet; a table with no rows becomes the empty note, as the drawer always drew it. */
export function toDetail(detail: OmniDetail): Detail {
  const tables = detail.tables.filter((table) => table.rows.length)
  const emptyTables = detail.tables.length - tables.length
  const wide = tables.some((table) => table.columns.length > 4)
  return {
    id: detail.id,
    title: detailTitle(detail),
    subtitle: detailSubtitle(detail),
    description: detail.description,
    stats: detail.strip?.map((item) => ({ label: sentenceCase(item.label), value: item.value, meta: item.meta })),
    fields: detail.fields.map((field) => ({ label: field.label, value: field.value === null || field.value === undefined ? null : String(field.value) })),
    sources: detail.sources,
    tables: tables.map(toTable),
    empty: detail.empty ?? (emptyTables ? OMNI_EMPTY_TABLE_TEXT : undefined),
    width: wide ? 'xl' : 'lg',
  }
}

const HOUR_MINUTE = new Intl.DateTimeFormat('en-US', { hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })
const DAY = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })

/** UTC axis labels: clock times within a day, calendar days beyond. */
export function timeAxisFormat(hours: number): (value: number) => string {
  return hours <= 24 ? (value) => HOUR_MINUTE.format(new Date(value)) : (value) => DAY.format(new Date(value))
}

/** A number shown as a KPI value tweens; formatted strings ('≥1,234', '22.7%') are shown as published. */
export function kpiValue(value: string): number | string | null {
  if (missing(value)) return null
  return /^\d{1,3}(,\d{3})*$/.test(value) ? Number(value.replace(/,/g, '')) : value
}

export function firstSentence(text: string): string {
  const match = /^.*?[.!?](?=\s|$)/.exec(text.trim())
  return match ? match[0] : text.trim()
}

/** Job counts on chart axes and tooltips; fractional axis ticks stay unlabeled. */
export const countLabel = (value: number): string => (Number.isInteger(value) ? value.toLocaleString('en-US') : '')
