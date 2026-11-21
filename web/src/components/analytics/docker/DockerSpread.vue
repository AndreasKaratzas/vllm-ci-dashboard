<script setup lang="ts">
import { useElementSize } from '@vueuse/core'
import { computed, ref } from 'vue'
import { tooltipHtml, type TooltipRow } from '@/components/charts/tooltip'
import { FULL_REBUILD_MIN_S, REUSED_MAX_S, type Spread } from '@/domain/dockerBuilds'
import { integer, seconds } from '@/domain/format'

export interface SpreadRow {
  key: string
  label: string
  spread: Spread
  previousP50: number | null
  note?: string
}

const props = defineProps<{ rows: SpreadRow[] }>()

const MIN = 60
const HOUR = 3600
const LO = 5
const TICKS = [10, 30, MIN, 2 * MIN, 5 * MIN, 10 * MIN, 30 * MIN, HOUR, 2 * HOUR, 4 * HOUR, 8 * HOUR, 12 * HOUR, 24 * HOUR]
const KEEP = new Set([MIN, 10 * MIN, HOUR, 2 * HOUR])
const PAD = 6

const plot = ref<HTMLElement | null>(null)
const { width } = useElementSize(plot)
const hi = computed(() => {
  const peak = Math.max(0, ...props.rows.map((row) => row.spread.max ?? 0))
  return peak <= 8 * HOUR ? 8 * HOUR : peak <= 12 * HOUR ? 12 * HOUR : 24 * HOUR
})
const x = (v: number) => {
  const clamped = Math.min(hi.value, Math.max(LO, v))
  return PAD + ((width.value - 2 * PAD) * Math.log(clamped / LO)) / Math.log(hi.value / LO)
}
const ticks = computed(() => {
  const inDomain = TICKS.filter((t) => t <= hi.value)
  const kept: number[] = []
  inDomain.forEach((t, i) => {
    const nextKeep = inDomain.slice(i + 1).find((n) => KEEP.has(n))
    const clear = (!kept.length || x(t) - x(kept.at(-1)!) >= 48) && (nextKeep === undefined || x(nextKeep) - x(t) >= 48)
    const end = t === hi.value && x(t) - x(kept.at(-1) ?? LO) >= 28
    if (KEEP.has(t) || clear || end) kept.push(t)
  })
  return kept
})
const REFS = [
  { value: REUSED_MAX_S, label: 'cache hit under 10 min', y: 24 },
  { value: FULL_REBUILD_MIN_S, label: 'full rebuild from 2 h', y: 11 },
]

const parts = (row: SpreadRow) => {
  const s = row.spread
  const text = !s.n
    ? ['No finished runs']
    : s.n < 5
      ? [`${s.n} ${s.n === 1 ? 'run' : 'runs'}: ${s.values.map(seconds).join(', ')}`]
      : [`n ${integer(s.n)}`, `min ${seconds(s.min)}`, `p25 ${seconds(s.p25)}`, `median ${seconds(s.p50)}`, `p75 ${seconds(s.p75)}`, `p90 ${seconds(s.p90)}`, `max ${seconds(s.max)}`]
  return row.note ? [...text, row.note] : text
}
const line = (row: SpreadRow) => parts(row).join(' · ')

const host = ref<HTMLElement | null>(null)
const tip = ref<{ key: string; x: number; y: number } | null>(null)
function show(event: Event, row: SpreadRow) {
  const root = host.value
  if (!root) return
  const box = (event.currentTarget as HTMLElement).getBoundingClientRect()
  const frame = root.getBoundingClientRect()
  tip.value = {
    key: row.key,
    x: Math.min(Math.max(box.left + box.width / 2 - frame.left, 90), frame.width - 90),
    y: box.bottom - frame.top,
  }
}
const tipHtml = computed(() => {
  const row = tip.value && props.rows.find((r) => r.key === tip.value!.key)
  if (!row) return null
  const s = row.spread
  const rows: TooltipRow[] =
    s.n >= 5
      ? [
          { label: 'Runs', value: integer(s.n) },
          { label: 'Fastest', value: seconds(s.min) },
          { label: 'p25', value: seconds(s.p25) },
          { label: 'Median', value: seconds(s.p50) },
          { label: 'p75', value: seconds(s.p75) },
          { label: 'p90', value: seconds(s.p90) },
          { label: 'Slowest', value: seconds(s.max) },
        ]
      : s.values.map((v, i) => ({ label: `Run ${i + 1}`, value: seconds(v) }))
  rows.push({ label: 'Previous median', value: row.previousP50 === null ? '—' : seconds(row.previousP50) })
  return tooltipHtml({ title: row.label, rows, hint: row.note })
})
const hide = () => (tip.value = null)
/** The previous median: a hollow caret above the whisker that points at its value, clear of the p90 dot. */
const caret = (cx: number, top = 0.75) => `M${cx - 4} ${top}L${cx + 4} ${top}L${cx} ${top + 5.5}Z`
</script>

<template>
  <div ref="host" class="relative flex min-w-0 flex-col gap-1" data-slot="docker-spread">
    <div class="grid grid-cols-1 gap-x-3 sm:grid-cols-[7rem_minmax(0,1fr)]">
      <span class="hidden sm:block" />
      <div ref="plot" class="min-w-0">
        <svg v-if="width" :width="width" height="30" class="block overflow-visible" aria-hidden="true">
          <template v-for="mark in REFS" :key="mark.value">
            <line :x1="x(mark.value)" :x2="x(mark.value)" :y1="mark.y - 8" y2="30" stroke="var(--muted-foreground)" stroke-width="1" />
            <text :x="x(mark.value) - 4" :y="mark.y" text-anchor="end" class="fill-muted-foreground text-[11px]">{{ mark.label }}</text>
          </template>
        </svg>
      </div>

      <template v-for="row in rows" :key="row.key">
        <span class="pt-1 text-sm font-medium sm:pt-1.5" data-slot="docker-spread-label">{{ row.label }}</span>
        <div
          class="min-w-0 rounded-sm outline-none focus-visible:ring-2 focus-visible:ring-ring/50"
          role="img"
          tabindex="0"
          :aria-label="`${row.label}: ${line(row)}`"
          data-slot="docker-spread-row"
          @mouseenter="show($event, row)"
          @focus="show($event, row)"
          @mouseleave="hide"
          @blur="hide"
        >
          <svg v-if="width" :width="width" height="28" class="block" aria-hidden="true">
            <line v-for="t in ticks" :key="t" :x1="x(t)" :x2="x(t)" y1="0" y2="28" stroke="var(--border)" stroke-width="1" />
            <line v-for="mark in REFS" :key="mark.value" :x1="x(mark.value)" :x2="x(mark.value)" y1="0" y2="28" stroke="var(--muted-foreground)" stroke-width="1" />
            <template v-if="row.spread.n >= 5">
              <line :x1="x(row.spread.min!)" :x2="x(row.spread.max!)" y1="14" y2="14" stroke="var(--muted-foreground)" stroke-width="1" />
              <rect
                :x="x(row.spread.p25!)"
                y="9"
                :width="Math.max(2, x(row.spread.p75!) - x(row.spread.p25!))"
                height="10"
                rx="4"
                fill="color-mix(in oklch, var(--muted-foreground) 25%, transparent)"
              />
              <path v-if="row.previousP50 !== null" :d="caret(x(row.previousP50))" fill="none" stroke="var(--muted-foreground)" stroke-width="1.5" stroke-linejoin="round" data-slot="docker-spread-previous" />
              <rect :x="x(row.spread.p50!) - 1" y="6" width="2" height="16" fill="var(--foreground)" />
              <circle :cx="x(row.spread.p90!)" cy="14" r="4" fill="var(--foreground)" stroke="var(--card)" stroke-width="2" />
            </template>
            <circle v-for="(v, i) in row.spread.values" v-else :key="i" :cx="x(v)" cy="14" r="4" fill="var(--foreground)" stroke="var(--card)" stroke-width="2" data-slot="docker-spread-dot" />
          </svg>
        </div>
        <span class="hidden sm:block" />
        <p class="pb-2 text-xs text-muted-foreground tabular-nums" :data-testid="`docker-spread-${row.key}`">
          <template v-for="(part, i) in parts(row)" :key="i">{{ i ? ' · ' : '' }}<span class="whitespace-nowrap">{{ part }}</span></template>
        </p>
      </template>

      <span class="hidden sm:block" />
      <svg v-if="width" :width="width" height="16" class="block overflow-visible" aria-hidden="true">
        <text v-for="t in ticks" :key="t" :x="x(t)" y="12" text-anchor="middle" class="fill-muted-foreground text-[11px] tabular-nums">{{ seconds(t) }}</text>
      </svg>
    </div>
    <p class="flex flex-wrap gap-x-3 gap-y-1 pt-1 text-xs text-muted-foreground sm:pl-[7.75rem]" data-slot="docker-spread-key">
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap"><svg width="18" height="12" aria-hidden="true"><rect x="0" y="1" width="18" height="10" rx="4" fill="color-mix(in oklch, var(--muted-foreground) 25%, transparent)" /></svg>p25–p75</span>
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap"><svg width="4" height="14" aria-hidden="true"><rect x="1" y="0" width="2" height="14" fill="var(--foreground)" /></svg>median</span>
      <span class="inline-flex items-center gap-1.5 whitespace-nowrap"><svg width="10" height="10" aria-hidden="true"><circle cx="5" cy="5" r="4" fill="var(--foreground)" /></svg>p90</span>
      <span v-if="rows.some((row) => row.previousP50 !== null)" class="inline-flex items-center gap-1.5 whitespace-nowrap"><svg width="10" height="8" aria-hidden="true"><path :d="caret(5, 1)" fill="none" stroke="var(--muted-foreground)" stroke-width="1.5" stroke-linejoin="round" /></svg>previous median</span>
    </p>
    <div
      v-if="tip && tipHtml"
      class="pointer-events-none absolute z-30 -translate-x-1/2 translate-y-2"
      :style="{ left: `${tip.x}px`, top: `${tip.y}px` }"
      data-slot="docker-spread-tooltip"
      aria-hidden="true"
      v-html="tipHtml"
    />
  </div>
</template>
