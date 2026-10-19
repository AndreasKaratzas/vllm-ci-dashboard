<script setup lang="ts">
import { VisAxis, VisCrosshair, VisLine, VisScatter, VisTooltip, VisXYContainer } from '@unovis/vue'
import { computed, ref } from 'vue'
import type { Rec } from '@/domain/format'
import { perfSparkLabel, perfSparkRows, perfValue } from '@/domain/perf'

// classic drawPerfSpark: finite points in payload order, colored by the metric
// status; a series with fewer than two points draws nothing.
const props = withDefaults(defineProps<{ series: Rec[]; status?: string; unit?: string; compact?: boolean; height?: number }>(), {
  status: undefined,
  unit: undefined,
  compact: false,
  height: 56,
})

const emit = defineEmits<{ point: [point: Rec] }>()

const rows = computed(() => perfSparkRows(props.series).map((point, index) => ({ index, point, value: Number(point.value) })))
type Row = (typeof rows.value)[number]
// At most eight evenly spaced commit labels, always including the newest point.
const ticks = computed(() => {
  const n = rows.value.length
  const step = Math.max(1, Math.ceil(n / 8))
  const picked = rows.value.filter((_, i) => i % step === 0).map((r) => r.index)
  const last = picked[picked.length - 1]
  if (n && last !== undefined && last !== n - 1) {
    if (n - 1 - last < step / 2) picked.pop()
    picked.push(n - 1)
  }
  return picked
})
const color = computed(() =>
  props.status === 'good' ? 'var(--success)' : props.status === 'bad' ? 'var(--destructive)' : 'var(--muted-foreground)',
)
// classic drawChart: clicking a point (nearest by index) or Enter on the focused
// chart opens that observation; arrow keys move the selection, starting at the newest.
const hovered = ref<number | null>(null)
const active = ref<number | null>(null)
function onMove(_x?: number | Date, datum?: Row) {
  hovered.value = datum ? datum.index : null
}
function inspect(index: number | null) {
  const row = index === null ? undefined : rows.value[index]
  if (row) emit('point', row.point)
}
function onKey(event: KeyboardEvent) {
  const last = rows.value.length - 1
  const current = active.value ?? last
  if (event.key === 'ArrowLeft' || event.key === 'ArrowUp') active.value = Math.max(0, current - 1)
  else if (event.key === 'ArrowRight' || event.key === 'ArrowDown') active.value = Math.min(last, current + 1)
  else if (event.key === 'Enter' || event.key === ' ') inspect(current)
  else return
  event.preventDefault()
  event.stopPropagation()
}
const activeLabel = computed(() => {
  const row = rows.value[active.value ?? rows.value.length - 1]
  return row ? perfSparkLabel(row.point) : ''
})
function tooltip(d: Row) {
  const label = d.point.build_number ? `Build #${d.point.build_number}` : perfSparkLabel(d.point)
  return `<div class="rounded-md border bg-background px-2 py-1 text-xs shadow-sm"><div class="text-muted-foreground">${label} · ${perfSparkLabel(d.point)}</div><div class="font-medium tabular-nums">${perfValue(d.value, props.unit)}</div></div>`
}
</script>

<template>
  <div
    v-if="rows.length >= 2"
    :style="{ height: `${height}px` }"
    class="w-full cursor-pointer rounded-md focus-visible:ring-2 focus-visible:ring-ring focus-visible:outline-none"
    data-testid="perf-chart"
    role="button"
    tabindex="0"
    aria-label="Performance metric history. Use arrow keys to choose an observation and Enter to inspect it."
    :aria-description="`Selected ${activeLabel}`"
    @click.stop="inspect(hovered)"
    @keydown="onKey"
    @mouseleave="hovered = null"
  >
    <VisXYContainer
      :data="rows"
      :height="height"
      :margin="compact ? { top: 4, bottom: 4, left: 2, right: 2 } : { top: 8, bottom: 8, left: 8, right: 16 }"
      :padding="compact ? {} : { top: 8, bottom: 8 }"
    >
      <VisLine :x="(d: Row) => d.index" :y="(d: Row) => d.value" :color="color" :line-width="2" curve-type="monotoneX" />
      <VisScatter v-if="!compact" :x="(d: Row) => d.index" :y="(d: Row) => d.value" :color="color" :size="5" />
      <template v-if="!compact">
        <VisAxis
          type="x"
          :tick-line="false"
          :domain-line="false"
          :grid-line="false"
          :tick-values="ticks"
          :tick-format="(i: number) => (rows[i] ? perfSparkLabel(rows[i]!.point) : '')"
        />
        <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" :tick-format="(v: number) => perfValue(v, unit)" />
      </template>
      <VisTooltip />
      <VisCrosshair :x="(d: Row) => d.index" :y="(d: Row) => d.value" :template="tooltip" :color="color" :onCrosshairMove="onMove" />
    </VisXYContainer>
  </div>
  <div v-else :style="{ height: `${height}px` }" class="flex items-center justify-center text-xs text-muted-foreground">
    Fewer than two numeric points
  </div>
</template>
