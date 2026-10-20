import type { ColumnSpec } from '@/components/table/columns'
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
}

export interface DetailField {
  label: string
  value: string | number | null | undefined
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
  /** Opening a row drills into this detail (the whole row is the hit target). */
  rowDetail?: (row: T) => Detail | undefined
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
  tables?: DetailTable<any>[]
  empty?: string
  trend?: DetailTrend
  /** Methodology that would otherwise push the data down; shown after it. */
  footnote?: string
  width?: 'md' | 'lg' | 'xl'
}
