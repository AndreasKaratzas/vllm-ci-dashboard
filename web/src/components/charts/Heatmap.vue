<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { SERIES, heatColor, heatLegend, heatLevel, type PaletteColor } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { plainNumber } from './chart'
import { tooltipHtml, type TooltipRow } from './tooltip'

export interface HeatmapColumn {
  id: string
  /** Short header text (e.g. the day of the month); empty for no label. */
  label: string
  /** Full name, read out and shown as the tooltip title. */
  title: string
}

export interface HeatmapCell {
  value: number
  rows?: TooltipRow[]
  /** Draws a ring marker in the cell, e.g. a co-failure event that day. */
  marker?: boolean
}

export interface HeatmapRow {
  id: string
  label: string
  /** Identity dot before the label (hardware), never a status. */
  dot?: PaletteColor
  meta?: string
  cells: HeatmapCell[]
  total: number
}

const props = withDefaults(
  defineProps<{
    columns: HeatmapColumn[]
    rows: HeatmapRow[]
    /** An aggregate row above the others with its own intensity scale (e.g. the whole fleet). */
    summary?: HeatmapRow
    label: string
    /** What a cell counts, for read-outs: "3 failures". */
    unit?: [string, string]
    totalLabel?: string
    /** Header above the row labels, e.g. the span of the columns. */
    corner?: string
    markerLabel?: string
    /** What selecting a row label does, read out after its total. */
    rowAction?: string
    /** What selecting a cell does, read out after its count. */
    cellAction?: string
    valueFormat?: (value: number) => string
    onCell?: (row: HeatmapRow, index: number) => void
    onRow?: (row: HeatmapRow) => void
    onColumn?: (index: number) => void
  }>(),
  { unit: () => ['failure', 'failures'], totalLabel: 'Total', rowAction: 'open the row', cellAction: 'open the cell', valueFormat: plainNumber },
)

const max = computed(() => Math.max(0, ...props.rows.flatMap((row) => row.cells.map((cell) => cell.value))))
const summaryMax = computed(() => Math.max(0, ...(props.summary?.cells.map((cell) => cell.value) ?? [])))
const totalMax = computed(() => Math.max(1, ...props.rows.map((row) => row.total)))
const legend = computed(() => heatLegend(max.value))
// The summary row is colored on its own scale, so the legend names both maxima.
const scaleNote = computed(
  () => `max ${props.valueFormat(max.value)} per cell` + (props.summary && summaryMax.value > 0 ? `; ${props.summary.label} on its own scale, max ${props.valueFormat(summaryMax.value)}` : ''),
)
const allRows = computed(() => (props.summary ? [props.summary, ...props.rows] : props.rows))
const counted = (value: number) => `${props.valueFormat(value)} ${value === 1 ? props.unit[0] : props.unit[1]}`
// Columns stay at least this wide; a long window packs tighter, then scrolls inside the card instead of the page.
const dense = computed(() => props.columns.length > 31)
const cellMin = computed(() => (dense.value ? '0.5rem' : '0.75rem'))
const gap = computed(() => (dense.value ? 2 : 3))
const grid = computed(() => ({
  gridTemplateColumns: `var(--heat-label) repeat(${props.columns.length}, minmax(${cellMin.value}, 3.5rem)) minmax(var(--heat-total), 18rem)`,
  gap: `${gap.value}px`,
}))
const minWidth = computed(() => `calc(var(--heat-label) + var(--heat-total) + ${props.columns.length} * (${cellMin.value} + ${gap.value}px))`)

const host = ref<HTMLElement | null>(null)
const scroller = ref<HTMLElement | null>(null)
// A window wider than the card opens on its newest days; the fade marks the older days to the left.
const olderHidden = ref(false)
function onScroll() {
  hide()
  olderHidden.value = (scroller.value?.scrollLeft ?? 0) > 1
}
function showNewest() {
  const el = scroller.value
  if (!el) return
  el.scrollLeft = el.scrollWidth
  olderHidden.value = el.scrollLeft > 1
}
onMounted(showNewest)
watch(() => props.columns.length, () => nextTick(showNewest))
const tip = ref<{ html: string; x: number; y: number; below: boolean } | null>(null)
function cellTitle(row: HeatmapRow, index: number) {
  return `${row.label} · ${props.columns[index]?.title ?? ''}`
}
function cellLabel(row: HeatmapRow, index: number) {
  const cell = row.cells[index]!
  return `${cellTitle(row, index)}: ${counted(cell.value)}${cell.marker && props.markerLabel ? `, ${props.markerLabel}` : ''}`
}
function show(event: Event, row: HeatmapRow, index: number) {
  const target = event.currentTarget as HTMLElement
  const root = host.value
  if (!root) return
  const box = target.getBoundingClientRect()
  const frame = root.getBoundingClientRect()
  const cell = row.cells[index]!
  const rows: TooltipRow[] = [{ label: props.unit[1].charAt(0).toUpperCase() + props.unit[1].slice(1), value: props.valueFormat(cell.value) }, ...(cell.rows ?? [])]
  if (cell.marker && props.markerLabel) rows.push({ label: props.markerLabel, value: 'yes' })
  const hint = props.onCell && cell.value > 0 ? `Click to ${props.cellAction}` : undefined
  tip.value = {
    html: tooltipHtml({ title: cellTitle(row, index), rows, hint }),
    x: Math.min(Math.max(box.left + box.width / 2 - frame.left, 80), frame.width - 80),
    y: box.top - frame.top,
    below: box.top - frame.top < 120,
  }
  if (tip.value.below) tip.value.y = box.bottom - frame.top
}
function hide() {
  tip.value = null
}
const isSummary = (rowIndex: number) => !!props.summary && rowIndex === 0
const rowOpens = (rowIndex: number) => !!props.onRow && !isSummary(rowIndex)
const clickable = (row: HeatmapRow | undefined, index: number) => !!props.onCell && (row?.cells[index]?.value ?? 0) > 0

// Roving focus: one cell is a tab stop and the arrow keys move between the cells with failures.
type Spot = [row: number, column: number]
const focused = ref<Spot | null>(null)
const firstSpot = computed<Spot | null>(() => {
  for (const [r, row] of allRows.value.entries()) {
    const c = row.cells.findIndex((_, index) => clickable(row, index))
    if (c >= 0) return [r, c]
  }
  return null
})
const tabStop = computed<Spot | null>(() => {
  const spot = focused.value
  return spot && clickable(allRows.value[spot[0]], spot[1]) ? spot : firstSpot.value
})
const isTabStop = (r: number, c: number) => tabStop.value?.[0] === r && tabStop.value?.[1] === c
function inRow(r: number, from: number, step: 1 | -1): number {
  const row = allRows.value[r]
  for (let c = from; row && c >= 0 && c < row.cells.length; c += step) if (clickable(row, c)) return c
  return -1
}
function nearestInRow(r: number, c: number): number {
  const row = allRows.value[r]
  if (!row) return -1
  for (let d = 0; d < row.cells.length; d++) {
    if (clickable(row, c - d)) return c - d
    if (clickable(row, c + d)) return c + d
  }
  return -1
}
function move(event: KeyboardEvent, r: number, c: number) {
  let target: Spot | null = null
  const along = (to: number) => (to >= 0 ? ([r, to] as Spot) : null)
  if (event.key === 'ArrowRight') target = along(inRow(r, c + 1, 1))
  else if (event.key === 'ArrowLeft') target = along(inRow(r, c - 1, -1))
  else if (event.key === 'Home') target = along(inRow(r, 0, 1))
  else if (event.key === 'End') target = along(inRow(r, (allRows.value[r]?.cells.length ?? 0) - 1, -1))
  else if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
    const step = event.key === 'ArrowDown' ? 1 : -1
    for (let next = r + step; next >= 0 && next < allRows.value.length && !target; next += step) {
      const to = nearestInRow(next, c)
      if (to >= 0) target = [next, to]
    }
  } else return
  event.preventDefault()
  if (!target) return
  focused.value = target
  host.value?.querySelector<HTMLElement>(`[data-spot="${target[0]}:${target[1]}"]`)?.focus()
}
</script>

<template>
  <div ref="host" class="relative flex min-w-0 flex-col gap-3 [--heat-label:8.5rem] [--heat-total:3.25rem] sm:[--heat-label:12rem] sm:[--heat-total:7rem]" data-slot="heatmap">
    <div class="relative">
      <div ref="scroller" class="-mx-1 overflow-x-auto px-1 pb-1" role="group" :aria-label="`${label}; arrow keys move between cells`" data-slot="heatmap-scroll" @scroll="onScroll">
        <div class="grid" :style="{ ...grid, minWidth }">
          <!-- Header: day columns -->
          <span class="sticky left-0 z-10 flex h-5 items-end bg-card text-[10px] leading-none text-muted-foreground" data-slot="heatmap-corner">{{ corner }}</span>
          <template v-for="(column, index) in columns" :key="column.id">
            <button
              v-if="onColumn"
              type="button"
              class="flex h-5 items-end justify-center rounded-sm text-[10px] leading-none text-muted-foreground tabular-nums outline-none hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring/50"
              :aria-label="`${column.title}: open the day`"
              :title="column.title"
              data-slot="heatmap-column"
              @click="onColumn(index)"
            >{{ column.label }}</button>
            <span v-else class="flex h-5 items-end justify-center text-[10px] leading-none text-muted-foreground tabular-nums" :title="column.title">{{ column.label }}</span>
          </template>
          <span class="flex h-5 items-end justify-end pl-2 text-[10px] leading-none font-medium text-muted-foreground">{{ totalLabel }}</span>

          <!-- Rows; the summary row reads on its own scale and sits apart from the ranked rows. -->
          <template v-for="(row, rowIndex) in allRows" :key="row.id">
            <component
              :is="rowOpens(rowIndex) ? 'button' : 'span'"
              :type="rowOpens(rowIndex) ? 'button' : undefined"
              :class="cn(
                'group/row sticky left-0 z-10 flex min-w-0 items-center gap-1.5 rounded-md bg-card pr-2 text-left outline-none',
                rowOpens(rowIndex) && 'hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring/50',
                isSummary(rowIndex) && 'mb-1.5 font-medium',
              )"
              :aria-label="rowOpens(rowIndex) ? `${row.label}: ${counted(row.total)}, ${rowAction}` : undefined"
              :data-slot="isSummary(rowIndex) ? 'heatmap-summary' : 'heatmap-row'"
              @click="rowOpens(rowIndex) && onRow!(row)"
            >
              <span v-if="row.dot" class="size-2 shrink-0 rounded-full" :style="{ backgroundColor: row.dot }" aria-hidden="true" />
              <span class="flex min-w-0 flex-col">
                <!-- Names truncate from the start, where nodes share their prefix. -->
                <span :class="cn('truncate text-xs', !isSummary(rowIndex) && 'font-mono [direction:rtl]')"><bdi>{{ row.label }}</bdi></span>
                <span v-if="row.meta" class="hidden truncate text-[10px] text-muted-foreground sm:block">{{ row.meta }}</span>
              </span>
            </component>
            <template v-for="(cell, index) in row.cells" :key="index">
              <component
                :is="clickable(row, index) ? 'button' : 'span'"
                :type="clickable(row, index) ? 'button' : undefined"
                :class="cn(
                  'relative flex h-6 items-center justify-center rounded-[4px] outline-none motion-safe:transition-[transform,box-shadow] motion-safe:duration-100',
                  isSummary(rowIndex) && 'mb-1.5',
                  cell.value > 0 && 'hover:z-20 hover:ring-2 hover:ring-foreground/70 focus-visible:z-20 focus-visible:ring-2 focus-visible:ring-ring motion-safe:hover:scale-110',
                )"
                :style="{ backgroundColor: heatColor(heatLevel(cell.value, isSummary(rowIndex) ? summaryMax : max)) }"
                :aria-label="clickable(row, index) ? `${cellLabel(row, index)}, ${cellAction}` : undefined"
                :aria-hidden="clickable(row, index) ? undefined : 'true'"
                :tabindex="clickable(row, index) ? (isTabStop(rowIndex, index) ? 0 : -1) : undefined"
                :data-spot="clickable(row, index) ? `${rowIndex}:${index}` : undefined"
                :data-value="cell.value"
                data-slot="heatmap-cell"
                @mouseenter="show($event, row, index)"
                @focus="(focused = [rowIndex, index]), show($event, row, index)"
                @keydown="move($event, rowIndex, index)"
                @mouseleave="hide"
                @blur="hide"
                @click="clickable(row, index) && onCell!(row, index)"
              >
                <span v-if="cell.marker" class="size-2 rounded-full bg-card ring-[1.5px] ring-foreground" data-slot="heatmap-marker" aria-hidden="true" />
              </component>
            </template>
            <span :class="cn('flex min-w-0 items-center justify-end gap-2 pl-2', isSummary(rowIndex) && 'mb-1.5')" data-slot="heatmap-total">
              <span v-if="!isSummary(rowIndex)" class="hidden h-1.5 min-w-0 flex-1 overflow-hidden rounded-full bg-muted sm:flex" aria-hidden="true">
                <span class="h-full rounded-full" :style="{ width: `${(row.total / totalMax) * 100}%`, backgroundColor: SERIES.primary }" />
              </span>
              <span class="shrink-0 text-xs font-semibold tabular-nums" :class="row.total === 0 && 'text-muted-foreground'">{{ valueFormat(row.total) }}</span>
            </span>
          </template>
        </div>
      </div>
      <span
        v-if="olderHidden"
        class="pointer-events-none absolute inset-y-0 left-[var(--heat-label)] z-10 w-6 bg-linear-to-r from-card to-transparent"
        aria-hidden="true"
        data-slot="heatmap-older"
      />
    </div>

    <div class="flex flex-wrap items-center justify-between gap-x-6 gap-y-2 text-xs text-muted-foreground" data-slot="heatmap-legend">
      <div class="flex items-center gap-2">
        <span>Fewer</span>
        <ul class="flex items-center gap-[3px]" :aria-label="`Color scale: ${legend.map((s) => (s.from === s.to ? s.from : `${s.from} to ${s.to}`)).join(', ')} ${unit[1]} per cell`">
          <li class="size-3.5 rounded-[3px]" :style="{ backgroundColor: heatColor(0) }" title="0" />
          <li v-for="step in legend" :key="step.level" class="size-3.5 rounded-[3px]" :style="{ backgroundColor: heatColor(step.level) }" :title="step.from === step.to ? `${step.from}` : `${step.from}–${step.to}`" />
        </ul>
        <span>More</span>
        <span v-if="max > 0" class="tabular-nums">({{ scaleNote }})</span>
      </div>
      <div class="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span v-if="markerLabel" class="flex items-center gap-1.5">
          <span class="size-2 rounded-full bg-card ring-[1.5px] ring-foreground" aria-hidden="true" />{{ markerLabel }}
        </span>
        <slot name="legend" />
        <span v-if="onCell" class="hidden items-center gap-1 sm:flex">Select a cell to {{ cellAction }} <ChevronRight class="size-3" aria-hidden="true" /></span>
      </div>
    </div>

    <div
      v-if="tip"
      class="pointer-events-none absolute z-30 -translate-x-1/2"
      :class="tip.below ? 'translate-y-2' : '-translate-y-full -mt-2'"
      :style="{ left: `${tip.x}px`, top: `${tip.y}px` }"
      data-slot="heatmap-tooltip"
      aria-hidden="true"
      v-html="tip.html"
    />
  </div>
</template>
