import { useElementSize } from '@vueuse/core'
import { computed, type Ref } from 'vue'
import type { ChartConfig } from '@/components/ui/chart'
import type { PaletteColor } from '@/lib/palette'
import { tooltipHtml } from './tooltip'

export type ChartRow = Record<string, unknown>

export interface SeriesSpec {
  key: string
  label: string
  color: PaletteColor
}

export interface InspectItem {
  label: string
  detail?: string
}

export function seriesValue(row: ChartRow, key: string): number {
  const value = Number(row[key])
  return Number.isFinite(value) ? value : 0
}

export function chartConfig(series: SeriesSpec[]): ChartConfig {
  return Object.fromEntries(series.map((s) => [s.key, { label: s.label, color: s.color }]))
}

const DAY = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' })
export const shortDay = (value: number | Date) => DAY.format(new Date(value))

export const plainNumber = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 1 })

/** At most `max` of the values, evenly spaced and always keeping the latest one. */
export function thinTicks(values: number[], max: number): number[] {
  const step = Math.max(1, Math.ceil(values.length / Math.max(1, max)))
  return values.filter((_, i) => (values.length - 1 - i) % step === 0)
}

/** About one axis tick per `spacing` pixels of the element's width. */
export function useTickCount(el: Ref<HTMLElement | null>, spacing = 80) {
  const { width } = useElementSize(el)
  return computed(() => Math.max(2, Math.floor(width.value / spacing)))
}

interface SeriesInteraction<T extends ChartRow> {
  rows: () => T[]
  series: () => SeriesSpec[]
  title: (row: T) => string
  format: (value: number) => string
  hint: () => string | undefined
  onSelect: () => ((row: T, index: number) => void) | undefined
}

/**
 * Crosshair tooltip, click-to-select and the keyboard Inspect list for a chart
 * whose rows carry one numeric value per series.
 */
export function useSeriesInteraction<T extends ChartRow>(opts: SeriesInteraction<T>) {
  let hovered: T | null = null
  const describe = (row: T) => opts.series().map((s) => ({ label: s.label, value: opts.format(seriesValue(row, s.key)), color: s.color }))
  function tooltip(row: T) {
    hovered = row
    const hint = opts.hint() ?? (opts.onSelect() ? 'Click for evidence' : undefined)
    return tooltipHtml({ title: opts.title(row), rows: describe(row), hint })
  }
  function select(row: T | null | undefined) {
    const handler = opts.onSelect()
    if (row && handler) handler(row, opts.rows().indexOf(row))
  }
  const inspectItems = (rows: T[]): InspectItem[] =>
    rows.map((row) => ({ label: opts.title(row), detail: describe(row).map((d) => d.value).join(' · ') }))
  return { tooltip, select, selectHovered: () => select(hovered), inspectItems }
}
