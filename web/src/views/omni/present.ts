import type { Detail, DetailTable as SheetTable, DetailTrend } from '@/components/evidence/detail'
import type { Delta } from '@/components/stat/DeltaBadge.vue'
import type { CellSpec, ColumnSpec } from '@/components/table/columns'
import type { Rec } from '@/domain/format'
import { OMNI_EMPTY_TABLE_TEXT, detailSubtitle, detailTitle, type Cell, type DetailTable, type KpiTrend, type OmniDetail } from '@/domain/omni'
import type { PaletteColor } from '@/lib/palette'

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

/** Rates and shares the domain withholds because the selected coverage is incomplete. */
export const WITHHELD = 'Unavailable'
export const WITHHELD_HINT = 'Unavailable: rates and shares need complete coverage of the selected window'
export const WITHHELD_LABEL = 'Needs complete coverage'

/** A domain value that means "no value" ('-', 'Unavailable') as a muted em dash, with the reason when there is one. */
export function shown(value: string): { text: string; hint?: string } {
  if (value === WITHHELD) return { text: '—', hint: WITHHELD_HINT }
  return { text: value === '-' || value === '' ? '—' : value }
}

export function toCell(cell: Cell): CellSpec {
  const open = cell.open
  const { text, hint } = shown(cell.text)
  return {
    text,
    title: hint,
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

export interface ColumnOptions<T> {
  /** Show every column (no column picker), as page previews do. */
  compact?: boolean
  /** Column label -> the breakpoint below which the column is hidden. */
  minWidth?: Record<string, 'sm' | 'md' | 'lg'>
  /** The whole row opens the first cell's detail, so no cell keeps its own drill-down. */
  rowClick?: boolean
  /** Column labels whose cells are identifiers (queues, pipelines), set in monospace. */
  mono?: string[]
  /** An inline magnitude bar in one numeric column. */
  bar?: {
    label: string
    value: (row: T) => number
    color: (row: T) => PaletteColor
  }
}

/** Literal classes so Tailwind emits them: a column hidden below a breakpoint. */
const HIDE_BELOW = { none: undefined, sm: 'hidden sm:table-cell', md: 'hidden md:table-cell', lg: 'hidden lg:table-cell' } as const

/**
 * The domain table as DataTable columns, without per-row noise: a badge column that reads the
 * same on every row is dropped, and a link repeating the row's first link loses its link.
 */
export function toColumns<T>(table: DetailTable<T>, options: ColumnOptions<T> = {}): ColumnSpec<T>[] {
  const firstHref = (row: T) => table.columns[0]?.cell(row).href
  const constant = (column: DetailTable<T>['columns'][number]) => {
    if (table.rows.length < 2) return false
    const cells = table.rows.map((row) => column.cell(row))
    return cells.every((cell) => cell.tone && cell.text === cells[0]!.text)
  }
  const bar = options.bar
  const max = bar ? Math.max(0, ...table.rows.map(bar.value)) : 0
  return table.columns
    .filter((column, index) => index === 0 || !constant(column))
    .map((column, index) => ({
      id: `${index}-${column.label}`,
      label: column.label,
      numeric: column.numeric,
      hideable: options.compact ? false : undefined,
      class: index ? HIDE_BELOW[options.minWidth?.[column.label] ?? 'none'] : undefined,
      value: (row: T) => sortKey(column.cell(row).text, column.numeric),
      cell: (row: T) => {
        const raw = column.cell(row)
        const cell = toCell(index > 0 && raw.href && raw.href === firstHref(row) ? { ...raw, href: undefined, open: undefined } : raw)
        if (options.mono?.includes(column.label)) cell.mono = true
        if (options.rowClick) cell.detail = undefined
        return bar && bar.label === column.label
          ? {
              ...cell,
              detail: undefined,
              bar: { value: bar.value(row), max, color: bar.color(row) },
            }
          : cell
      },
    }))
}

/** The detail the first cell of a row opens, so the whole row can open it. */
export function rowDetail<T>(table: DetailTable<T>): ((row: T) => Detail | undefined) | undefined {
  const first = table.columns[0]
  if (!first || !table.rows.some((row) => first.cell(row).open)) return undefined
  return (row: T) => {
    const open = first.cell(row).open
    return open ? toDetail(open()) : undefined
  }
}

/** Context columns a phone drops; a table keeps its subject, its main count and its state. */
export const PHONE_COLUMNS = {
  Queue: 'sm',
  Build: 'lg',
  Started: 'sm',
  'Omni started': 'sm',
  'GPU-slot requests': 'sm',
  'Omni GPU-slot requests': 'sm',
  'GPU-hours': 'sm',
  'Omni GPU-hours': 'sm',
  Analysis: 'md',
  Source: 'sm',
} as const

function toTable(table: DetailTable<Rec>): SheetTable<Rec> {
  const columns = toColumns(table, { minWidth: PHONE_COLUMNS })
  return {
    title: table.title,
    meta: table.meta,
    columns: columns.length <= 5 ? columns.map((column) => ({ ...column, hideable: false })) : columns,
    rows: table.rows,
    searchText: table.searchText,
    emptyText: OMNI_EMPTY_TABLE_TEXT,
    onRowClick: rowDetail(table),
  }
}

const LONG_DESCRIPTION = 160

/**
 * An Omni drawer as an evidence sheet. When no table has rows the sheet shows one empty note, as the
 * drawer did; a long methodology description moves below the data.
 */
export function toDetail(detail: OmniDetail, trend?: DetailTrend): Detail {
  const anyRows = detail.tables.some((table) => table.rows.length)
  const tables = anyRows ? detail.tables : []
  const wide = tables.some((table) => table.columns.length > 4)
  const long = (detail.description?.length ?? 0) > LONG_DESCRIPTION
  return {
    id: detail.id,
    title: detailTitle(detail),
    subtitle: detailSubtitle(detail),
    description: long ? undefined : detail.description,
    footnote: long ? detail.description : undefined,
    stats: detail.strip?.map((item) => ({
      label: sentenceCase(item.label),
      value: item.value,
      meta: item.meta,
    })),
    fields: detail.fields.map((field) => {
      if (field.value === null || field.value === undefined) return { label: field.label, value: null }
      const { text, hint } = shown(String(field.value))
      return { label: field.label, value: text, hint }
    }),
    sources: detail.sources,
    tables: tables.map(toTable),
    empty: detail.empty ?? (detail.tables.length && !anyRows ? OMNI_EMPTY_TABLE_TEXT : undefined),
    trend,
    width: wide ? 'xl' : 'lg',
  }
}

/** A number shown as a KPI value tweens; formatted strings ('≥1,234', '22.7%') are shown as published. */
export function kpiValue(value: string): number | string | null {
  if (shown(value).text === '—') return null
  return /^\d{1,3}(,\d{3})*$/.test(value) ? Number(value.replace(/,/g, '')) : value
}

export function firstSentence(text: string): string {
  const match = /^.*?[.!?](?=\s|$)/.exec(text.trim())
  return match ? match[0] : text.trim()
}

export const DELTA_BASELINE = 'vs the first half of the window'

/** A KPI change as a badge: relative for counts, percentage points for shares. */
export function toDelta(change: KpiTrend['change']): Delta | undefined {
  if (!change) return undefined
  const sign = change.value > 0 ? '+' : ''
  const text = change.kind === 'percent' ? `${sign}${change.value.toFixed(0)}%` : `${sign}${change.value.toFixed(1)} pp`
  return { value: change.value, text, label: DELTA_BASELINE }
}

/** Spacing beyond `factor` times the median step between sorted times is a collection gap. */
export function collectionGap(times: number[], factor: number): number | undefined {
  const steps = times
    .slice(1)
    .map((time, i) => time - times[i]!)
    .filter((step) => step > 0)
    .sort((a, b) => a - b)
  if (!steps.length) return undefined
  return steps[Math.floor(steps.length / 2)]! * factor
}

/** Job counts on chart axes and tooltips; fractional axis ticks stay unlabeled. */
export const countLabel = (value: number): string => (Number.isInteger(value) ? value.toLocaleString('en-US') : '')
