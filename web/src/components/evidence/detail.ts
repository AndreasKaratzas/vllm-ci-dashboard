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
  width?: 'md' | 'lg' | 'xl'
}
