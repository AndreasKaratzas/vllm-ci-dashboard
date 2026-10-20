import type { Detail } from '@/components/evidence/detail'
import type { PaletteColor } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

export interface CellSpec {
  text: string
  /** External link; opens in a new tab. */
  href?: string
  /** Drill-down evidence opened in the sheet stack. */
  detail?: () => Detail
  onOpen?: () => void
  tone?: Tone
  mono?: boolean
  /** De-emphasized value, e.g. a zero count. */
  muted?: boolean
  sub?: string
  /** Several external links in one cell, e.g. the exact jobs behind one observation; `text` shows when there are none. */
  links?: { label: string; url: string }[]
  /** Short labels rendered as outline badges, e.g. issue labels. */
  tags?: string[]
  /** Identity dots before the text (hardware family, series), never a status. */
  dots?: PaletteColor[]
  nowrap?: boolean
  /** A trend drawn after the text; fewer than two values draw nothing. */
  spark?: { values: number[]; label: string; color?: PaletteColor }
  /** Tooltip on the cell text, e.g. why a value is shown as a muted dash. */
  title?: string
  /** An inline magnitude bar before the text, `value` of `max`. */
  bar?: { value: number; max: number; color: PaletteColor }
}

export interface ColumnSpec<T> {
  id: string
  label: string
  /** Sort and search key. */
  value: (row: T) => string | number
  cell?: (row: T) => CellSpec
  /** Replaces the default ordering of `value` (numbers by difference, text by numeric-aware collation). */
  compare?: (a: T, b: T) => number
  numeric?: boolean
  sortable?: boolean
  hideable?: boolean
  /** Classes for the column's header and cells, e.g. 'hidden md:table-cell' or 'whitespace-nowrap'. */
  class?: string
}

export interface FilterSpec<T> {
  id: string
  label: string
  options: { value: string; label: string }[]
  /** Label of the no-filter option; defaults to "All <label>". */
  allLabel?: string
  initial?: string
  predicate: (row: T, value: string) => boolean
}

const show = (value: unknown) => (value === null || value === undefined || value === '' ? '—' : String(value))

export const text = (value: unknown, sub?: string): CellSpec => ({ text: show(value), sub })
export const mono = (value: unknown): CellSpec => ({ text: show(value), mono: true })
export const num = (value: number | null | undefined, format: (n: number) => string = (n) => n.toLocaleString('en-US')): CellSpec => ({
  text: value === null || value === undefined || !Number.isFinite(value) ? '—' : format(value),
})
export const link = (value: unknown, url: string | null | undefined, isMono = false): CellSpec => ({ text: show(value), href: url || undefined, mono: isMono })
export const badge = (value: unknown, tone: Tone): CellSpec => ({ text: show(value), tone })
export const dotted = (value: unknown, color: PaletteColor, sub?: string): CellSpec => ({ text: show(value), dots: [color], sub })
export const drill = (value: unknown, detail: () => Detail): CellSpec => ({ text: show(value), detail })
