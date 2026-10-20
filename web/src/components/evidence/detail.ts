import type { Component } from 'vue'
import type { ColumnSpec, FilterSpec } from '@/components/table/columns'
import { rateText } from '@/domain/format'
import { rateStatus } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

export interface DetailNote {
  tone: Tone
  title?: string
  text: string
}

export interface DetailStat {
  label: string
  value: string
  meta?: string
  tone?: Tone
  /** Status badge label when the tone's default label would misname the state (e.g. "Attention", "Stale"). */
  toneLabel?: string
  /** Tint the value with its tone instead of adding a badge, e.g. for a failure count. */
  tint?: boolean
}

/** A pass-rate stat: quiet at a pass, otherwise badged with the band it falls in ("Below 95%"). */
export function rateStat(label: string, percent: number | null | undefined, meta?: string): DetailStat {
  const status = rateStatus(percent)
  return { label, value: rateText(percent), meta, tone: status?.tone, toneLabel: status?.label }
}

export interface DetailField {
  label: string
  value: string | number | null | undefined
  /** Render the value as a status badge of this tone. */
  tone?: Tone
  /** Render the value as an external link. */
  href?: string
  mono?: boolean
  /** Why the value is missing, shown as the tooltip of its muted dash. */
  hint?: string
}

export interface DetailSource {
  label: string
  url: string
}

export interface DetailTable<T = unknown> {
  title?: string
  meta?: string
  columns: ColumnSpec<T>[]
  rows: T[]
  searchText?: (row: T) => string
  emptyText?: string
  pageSize?: number | false
  filters?: FilterSpec<T>[]
  searchPlaceholder?: string
  /** Makes rows clickable; a returned Detail opens above the current sheet. */
  onRowClick?: (row: T) => Detail | void
  /** Count rows as "shown of total" instead of "shown". */
  showTotal?: boolean
}

/** A custom block (chart, history strip, loader) rendered between the fields and the tables. */
export interface DetailSection {
  title?: string
  meta?: string
  component: Component
  props?: Record<string, unknown>
}

/** A small trend panel for the sheet's subject, oldest value first. */
export interface DetailTrend {
  title: string
  meta?: string
  values: number[]
  /** Labels of the first and last value. */
  range?: [string, string]
}

/** Everything an evidence sheet shows; domain builders return it and stay UI-free. */
export interface Detail {
  id: string
  title: string
  subtitle?: string
  description?: string
  notes?: DetailNote[]
  stats?: DetailStat[]
  fields?: DetailField[]
  sources?: DetailSource[]
  sections?: DetailSection[]
  tables?: DetailTable<any>[]
  empty?: string
  trend?: DetailTrend
  /** Methodology that would otherwise push the data down; shown after it. */
  footnote?: string
  width?: 'md' | 'lg' | 'xl'
}
