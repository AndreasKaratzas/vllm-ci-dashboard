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
const DAY_TIME = new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23', timeZone: 'UTC' })
export const shortDayTime = (value: number | Date) => DAY_TIME.format(new Date(value))

export const plainNumber = (value: number) => value.toLocaleString('en-US', { maximumFractionDigits: 1 })

/**
 * Round ticks from at or below `min` to at or beyond `max`: about `count` steps of 1, 2, 2.5 or 5
 * times a power of ten, never finer than `minStep` (1 for counts).
 */
export function niceTicks(min: number, max: number, count: number, minStep = 0): number[] {
  const span = max - min
  if (!(span > 0)) return [min, min + Math.max(1, minStep)]
  const raw = Math.max(span / Math.max(1, count), minStep)
  const power = 10 ** Math.floor(Math.log10(raw))
  const step = [1, 2, 2.5, 5, 10].map((m) => m * power).find((candidate) => candidate >= raw)!
  const ticks: number[] = []
  for (let tick = Math.floor(min / step) * step; tick < max + step / 2; tick += step) ticks.push(Math.round(tick * 1e9) / 1e9)
  if (ticks.at(-1)! < max) ticks.push(Math.round((ticks.at(-1)! + step) * 1e9) / 1e9)
  return ticks
}

/** At most `max` of the values, evenly spaced and always keeping the latest one. */
export function thinTicks(values: number[], max: number): number[] {
  const step = Math.max(1, Math.ceil(values.length / Math.max(1, max)))
  return values.filter((_, i) => (values.length - 1 - i) % step === 0)
}

/** Up to `count` evenly spaced ticks across the domain, dropping any whose label repeats the previous one. */
export function distinctTicks([min, max]: [number, number], count: number, format: (value: number) => string): number[] {
  const steps = Math.max(1, count - 1)
  const ticks: number[] = []
  let last: string | null = null
  for (let i = 0; i <= steps; i++) {
    const value = min + ((max - min) * i) / steps
    const label = format(value)
    if (label !== last) ticks.push(value)
    last = label
  }
  return ticks
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
  // Unovis reports an undefined x whenever the pointer is outside the plot area.
  function track(x: number | Date | undefined, row: T | undefined) {
    hovered = x === undefined ? null : (row ?? null)
  }
  function tooltip(row: T) {
    const hint = opts.hint() ?? (opts.onSelect() ? 'Click for evidence' : undefined)
    return tooltipHtml({ title: opts.title(row), rows: describe(row), hint })
  }
  function select(row: T | null | undefined) {
    const handler = opts.onSelect()
    if (row && handler) handler(row, opts.rows().indexOf(row))
  }
  const inspectItems = (rows: T[]): InspectItem[] =>
    rows.map((row) => ({ label: opts.title(row), detail: describe(row).map((d) => d.value).join(' · ') }))
  return { track, tooltip, select, selectHovered: () => select(hovered), inspectItems }
}
