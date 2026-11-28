<script setup lang="ts">
import { computed } from 'vue'
import { SERIES, type PaletteColor } from '@/lib/palette'

const props = withDefaults(defineProps<{ values: number[]; label: string; color?: PaletteColor }>(), { color: () => SERIES.primary })

const W = 100
const H = 32
const PAD = 2

const points = computed(() => {
  const values = props.values.filter((v) => Number.isFinite(v))
  if (values.length < 2) return []
  const min = Math.min(...values)
  const span = Math.max(...values) - min || 1
  return values.map((v, i) => ({ x: (i / (values.length - 1)) * W, y: PAD + (1 - (v - min) / span) * (H - PAD * 2) }))
})
const line = computed(() => points.value.map((p, i) => `${i ? 'L' : 'M'}${p.x.toFixed(2)},${p.y.toFixed(2)}`).join(''))
const area = computed(() => (points.value.length ? `${line.value}L${W},${H}L0,${H}Z` : ''))
const last = computed(() => points.value.at(-1))
</script>

<template>
  <div v-if="points.length" class="relative h-10 w-full" role="img" :aria-label="label" data-slot="sparkline">
    <svg :viewBox="`0 0 ${W} ${H}`" preserveAspectRatio="none" class="size-full overflow-visible" aria-hidden="true">
      <path :d="area" :fill="color" fill-opacity="0.15" />
      <path :d="line" fill="none" :stroke="color" stroke-width="2" vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round" />
    </svg>
    <span
      v-if="last"
      class="absolute size-2 -translate-x-1/2 -translate-y-1/2 rounded-full ring-2 ring-card"
      :style="{ left: `${last.x}%`, top: `${(last.y / H) * 100}%`, backgroundColor: color }"
      aria-hidden="true"
    />
  </div>
</template>
