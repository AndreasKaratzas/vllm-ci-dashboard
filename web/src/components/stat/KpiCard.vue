<script setup lang="ts">
import { ChevronRight, CircleDashed, ExternalLink } from '@lucide/vue'
import { TransitionPresets, usePreferredReducedMotion, useTransition } from '@vueuse/core'
import { computed } from 'vue'
import { RouterLink, type RouteLocationRaw } from 'vue-router'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import { Card, CardAction, CardDescription, CardFooter, CardHeader, CardTitle } from '@/components/ui/card'
import { rateTone, toneClasses } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import { cn } from '@/lib/utils'
import DeltaBadge, { type Delta } from './DeltaBadge.vue'
import Sparkline from './Sparkline.vue'

export interface KpiAction {
  label: string
  to?: RouteLocationRaw
  href?: string
  onOpen?: () => void
}

const props = withDefaults(
  defineProps<{
    label: string
    value: number | string | null
    format?: (value: number) => string
    unit?: string
    delta?: Delta
    spark?: number[]
    status?: { tone: Tone; label?: string }
    headline?: string
    footnote?: string
    action?: KpiAction
    variant?: 'default' | 'ring'
    ratio?: { current: number; total: number }
    /** Shown in place of the value and trend when the metric cannot be established; the footnote gives the reason. */
    empty?: string
  }>(),
  { variant: 'default', format: (value: number) => Math.round(value).toLocaleString('en-US') },
)

const reducedMotion = usePreferredReducedMotion()
const numeric = computed(() => (typeof props.value === 'number' && Number.isFinite(props.value) ? props.value : 0))
const tweened = useTransition(numeric, {
  duration: computed(() => (reducedMotion.value === 'reduce' ? 0 : 400)),
  transition: TransitionPresets.easeOutCubic,
})
const display = computed(() => {
  if (props.value === null || props.value === '') return '—'
  if (typeof props.value === 'string') return props.value
  return Number.isFinite(props.value) ? props.format(tweened.value) : '—'
})
const missing = computed(() => display.value === '—')
// Zero counts render muted like missing values.
const muted = computed(() => missing.value || props.value === 0)

const RADIUS = 42
const CIRCUMFERENCE = 2 * Math.PI * RADIUS
const percent = computed(() =>
  props.ratio && props.ratio.total > 0 ? Math.min(100, (Math.max(0, props.ratio.current) / props.ratio.total) * 100) : null,
)
const ringTone = computed(() => props.status?.tone ?? rateTone(percent.value))

const actionTag = computed(() => (props.action?.to ? RouterLink : props.action?.href ? 'a' : 'button'))
const actionAttrs = computed(() => {
  const action = props.action
  if (!action) return {}
  if (action.to) return { to: action.to }
  if (action.href) return { href: action.href, target: '_blank', rel: 'noopener' }
  return { type: 'button', onClick: action.onOpen }
})
</script>

<template>
  <Card
    :class="cn(
      '@container/card relative gap-4 bg-linear-to-t from-brand/5 to-card py-5 shadow-xs motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300',
      action && 'group/kpi transition-[border-color,box-shadow,transform] duration-150 hover:border-foreground/20 hover:shadow-sm has-[[data-kpi-action]:focus-visible]:ring-3 has-[[data-kpi-action]:focus-visible]:ring-ring/50 motion-safe:active:scale-[.99]',
    )"
    data-slot="kpi-card"
  >
    <CardHeader class="px-4 @max-[15rem]/card:has-data-[slot=card-action]:grid-cols-1 @[15rem]/card:px-6">
      <CardDescription class="min-w-0">
        <component
          :is="actionTag"
          v-if="action"
          v-bind="actionAttrs"
          data-kpi-action
          class="text-left outline-none after:absolute after:inset-0 after:rounded-xl after:content-['']"
        >
          {{ label }}<span class="sr-only">: {{ action.label }}</span>
        </component>
        <template v-else>{{ label }}</template>
      </CardDescription>
      <CardTitle v-if="empty" class="pt-1" data-slot="kpi-empty">
        <span class="inline-flex items-center gap-2 rounded-md border border-dashed px-2.5 py-1.5 text-sm font-medium text-muted-foreground">
          <CircleDashed class="size-4 shrink-0" aria-hidden="true" />{{ empty }}
        </span>
      </CardTitle>
      <CardTitle
        v-else-if="variant === 'default'"
        class="font-semibold tabular-nums"
        :class="[muted && 'text-muted-foreground', display.length > 10 ? 'text-xl @[250px]/card:text-2xl' : 'text-2xl @[250px]/card:text-3xl']"
        data-slot="kpi-value"
      >
        {{ display }}<span v-if="unit && !missing" class="ml-1 text-base font-medium text-muted-foreground">{{ unit }}</span>
      </CardTitle>
      <CardAction v-if="delta || status" class="@max-[15rem]/card:col-start-1 @max-[15rem]/card:row-span-1 @max-[15rem]/card:row-start-3 @max-[15rem]/card:justify-self-start flex flex-wrap items-center justify-end gap-1.5">
        <StatusBadge v-if="status" :tone="status.tone" :label="status.label" />
        <DeltaBadge v-if="delta" :delta="delta" />
      </CardAction>
    </CardHeader>

    <div v-if="variant === 'ring'" class="flex flex-col items-start gap-3 px-4 @[15rem]/card:flex-row @[15rem]/card:items-center @[15rem]/card:gap-4 @[15rem]/card:px-6">
      <div class="relative size-20 shrink-0 @[250px]/card:size-24" role="img" :aria-label="percent === null ? `${label}: unavailable` : `${label}: ${percent.toFixed(1)}%`">
        <svg viewBox="0 0 100 100" class="size-full -rotate-90" aria-hidden="true">
          <circle cx="50" cy="50" :r="RADIUS" class="fill-none stroke-muted" stroke-width="10" />
          <circle
            v-if="percent !== null"
            cx="50"
            cy="50"
            :r="RADIUS"
            class="fill-none motion-safe:transition-[stroke-dashoffset] motion-safe:duration-700"
            :class="toneClasses(ringTone, 'stroke')"
            stroke-width="10"
            stroke-linecap="round"
            :stroke-dasharray="CIRCUMFERENCE"
            :stroke-dashoffset="CIRCUMFERENCE * (1 - percent / 100)"
          />
        </svg>
        <span class="absolute inset-0 flex items-center justify-center text-lg font-semibold tabular-nums" :class="percent === null && 'text-muted-foreground'" data-slot="kpi-value">
          {{ percent === null ? '—' : `${percent.toFixed(1)}%` }}
        </span>
      </div>
      <div class="flex min-w-0 flex-col gap-0.5">
        <span class="text-xl font-semibold break-words tabular-nums" :class="muted && 'text-muted-foreground'">{{ display }}</span>
        <span v-if="ratio" class="text-xs text-muted-foreground tabular-nums">
          {{ ratio.current.toLocaleString('en-US') }} / {{ ratio.total.toLocaleString('en-US') }}
        </span>
      </div>
    </div>

    <div v-if="spark && spark.length > 1 && !empty" class="px-4 @[15rem]/card:px-6">
      <Sparkline :values="spark" :label="`${label} trend over ${spark.length} points`" />
    </div>

    <CardFooter v-if="headline || footnote || action" class="flex-col items-start gap-1 px-4 text-sm @[15rem]/card:px-6">
      <div v-if="headline || action" class="flex w-full items-center gap-2 font-medium">
        <span class="line-clamp-2 min-w-0 flex-1">{{ headline ?? action?.label }}</span>
        <component
          :is="action?.href ? ExternalLink : ChevronRight"
          v-if="action"
          class="size-4 shrink-0 text-muted-foreground transition-transform group-hover/kpi:translate-x-0.5 group-hover/kpi:text-foreground"
          aria-hidden="true"
        />
      </div>
      <p v-if="footnote" class="hidden text-muted-foreground @[15rem]/card:block">{{ footnote }}</p>
    </CardFooter>
  </Card>
</template>
