<script setup lang="ts">
import { useElementSize } from '@vueuse/core'
import { computed, nextTick, ref } from 'vue'
import { shortDayTime } from '@/components/charts/chart'
import { integer } from '@/domain/format'
import { buildOutcome, buildTone, type MainBuild } from '@/domain/mainCi'
import { TONE_LABEL } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { cn } from '@/lib/utils'

const props = withDefaults(defineProps<{ builds: MainBuild[]; label?: string; max?: number }>(), { label: 'Latest main builds', max: 60 })
const emit = defineEmits<{ select: [MainBuild] }>()

// As many tiles as fit at 8px with a 3px gap, so phones show the newest builds rather than slivers.
const TILE = 8
const GAP = 3
const host = ref<HTMLElement | null>(null)
const { width } = useElementSize(host)
const count = computed(() => Math.max(12, Math.min(props.max, Math.floor((width.value + GAP) / (TILE + GAP)) || props.max)))
// Oldest on the left, so the strip reads like a timeline ending at the newest build.
const shown = computed(() => props.builds.slice(0, count.value).reverse())

// Tile height follows the build's AMD job count (square root, from a third), so full CI runs stand out from small ones.
const most = computed(() => Math.max(1, ...shown.value.map((build) => jobs(build))))
function jobs(build: MainBuild) {
  const amd = build.amd
  return amd ? amd.passed + amd.soft + amd.hard : 0
}
const height = (build: MainBuild) => `${Math.round((0.35 + 0.65 * Math.sqrt(jobs(build) / most.value)) * 100)}%`

// Pass is quiet (a faint tile); only failures carry a full status fill.
const FILL: Record<Tone, string> = {
  pass: 'bg-status-pass/40 hover:bg-status-pass/70',
  soft: 'bg-status-soft hover:bg-status-soft/80',
  hard: 'bg-status-hard hover:bg-status-hard/80',
  info: 'bg-muted-foreground/25',
  neutral: 'bg-muted-foreground/25 hover:bg-muted-foreground/40',
  'not-targeted': 'bg-muted-foreground/25',
}

const active = ref<number | null>(null)
const focusIndex = ref<number | null>(null)
const current = computed(() => shown.value[active.value ?? shown.value.length - 1])
const tabStop = computed(() => focusIndex.value ?? shown.value.length - 1)
const tiles = ref<HTMLButtonElement[]>([])

function describe(build: MainBuild) {
  const amd = build.amd
  const counts = amd ? `${integer(amd.passed)} passed, ${integer(amd.hard)} hard, ${integer(amd.soft)} soft, ${integer(amd.retried)} retried` : 'no AMD job counts'
  return `Build #${build.number}, ${shortDayTime(Date.parse(build.createdAt))}: ${buildOutcome(build)}; ${counts}`
}
async function move(index: number) {
  const next = Math.max(0, Math.min(shown.value.length - 1, index))
  focusIndex.value = next
  active.value = next
  await nextTick()
  tiles.value[next]?.focus()
}
function onKey(event: KeyboardEvent, index: number) {
  const target = { ArrowLeft: index - 1, ArrowRight: index + 1, Home: 0, End: shown.value.length - 1 }[event.key]
  if (target === undefined) return
  event.preventDefault()
  void move(target)
}
</script>

<template>
  <div class="flex min-w-0 flex-col gap-3" data-testid="main-build-strip">
    <div ref="host" role="toolbar" :aria-label="label" class="flex h-14 w-full items-end" :style="{ gap: `${GAP}px` }" @mouseleave="active = null">
      <button
        v-for="(build, index) in shown"
        :key="build.number"
        ref="tiles"
        type="button"
        :tabindex="index === tabStop ? 0 : -1"
        :aria-label="describe(build)"
        :data-tone="buildTone(build)"
        data-slot="main-build-tile"
        :class="
          cn(
            'min-w-0 flex-1 cursor-pointer rounded-[3px] outline-none transition-[background-color,transform] duration-150 focus-visible:ring-3 focus-visible:ring-ring/60 motion-safe:hover:-translate-y-0.5',
            FILL[buildTone(build)],
            active === index && 'ring-2 ring-foreground/40',
          )
        "
        :style="{ height: height(build) }"
        @mouseenter="active = index"
        @focus="active = index; focusIndex = index"
        @keydown="onKey($event, index)"
        @click="emit('select', build)"
      />
    </div>
    <div class="flex min-h-10 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-xs text-muted-foreground" aria-live="polite">
      <p v-if="current" class="min-w-0 flex-1" data-slot="main-build-readout">
        <span class="font-mono font-medium text-foreground">#{{ current.number }}</span>
        · {{ shortDayTime(Date.parse(current.createdAt)) }} ·
        <span :class="buildTone(current) === 'hard' ? 'text-status-hard-fg' : buildTone(current) === 'soft' ? 'text-status-soft-fg' : 'text-foreground'">{{ buildOutcome(current) }}</span>
        <template v-if="current.amd"> · {{ integer(current.amd.passed) }} passed<template v-if="current.amd.retried"> · {{ integer(current.amd.retried) }} retried</template></template>
        <span class="block truncate">{{ current.headline }}</span>
      </p>
      <ul class="flex shrink-0 items-center gap-3" aria-label="Tile colors">
        <li v-for="tone in (['pass', 'soft', 'hard', 'neutral'] as const)" :key="tone" class="inline-flex items-center gap-1.5">
          <span class="size-2.5 rounded-[2px]" :class="FILL[tone]" aria-hidden="true" />{{ tone === 'neutral' ? 'No AMD result' : TONE_LABEL[tone] }}
        </li>
      </ul>
    </div>
  </div>
</template>
