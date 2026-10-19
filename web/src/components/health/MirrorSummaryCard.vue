<script setup lang="ts">
// Classic AmdMirrorInventory.summaryCard on the CI Health Overview.
import { ArrowRight, GitFork } from '@lucide/vue'
import { computed } from 'vue'
import { Card, CardContent } from '@/components/ui/card'
import type { Rec } from '@/domain/format'
import { amdMirrorSummary } from '@/domain/mirrors'
import MirrorModeTrack from './MirrorModeTrack.vue'

const props = defineProps<{ inventory: Rec | null | undefined; runtimeGroupCount: number | null }>()
defineEmits<{ open: [] }>()
const summary = computed(() => amdMirrorSummary(props.inventory || {}, props.runtimeGroupCount))
</script>

<template>
  <Card class="transition-colors hover:bg-accent/40" :class="summary.unavailable ? 'opacity-80' : ''">
    <CardContent>
      <button
        type="button"
        class="grid w-full grid-cols-[auto_minmax(0,1fr)] items-center gap-x-5 gap-y-3 text-left @3xl/main:grid-cols-[auto_minmax(0,1fr)_minmax(0,18rem)_auto]"
        :aria-label="summary.ariaLabel"
        data-testid="mirror-summary"
        @click="$emit('open')"
      >
        <span class="flex size-12 items-center justify-center rounded-xl bg-primary/10 text-primary" aria-hidden="true">
          <GitFork class="size-6" />
        </span>
        <span class="flex min-w-0 flex-col gap-0.5">
          <span class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">AMD mirror configuration on main</span>
          <span class="flex items-baseline gap-2">
            <strong class="text-2xl font-semibold tabular-nums" data-testid="mirror-summary-count">{{ summary.countLabel }}</strong>
            <span class="font-medium">configured AMD mirror groups</span>
          </span>
          <span class="text-sm text-muted-foreground" data-testid="mirror-summary-meta">{{ summary.meta }}</span>
        </span>
        <span class="col-span-2 flex flex-col gap-2 @3xl/main:col-span-1">
          <MirrorModeTrack v-if="summary.track" :segments="summary.track" />
          <span class="text-sm text-muted-foreground" data-testid="mirror-summary-facts">{{ summary.facts }}</span>
        </span>
        <span class="col-span-2 inline-flex items-center gap-1 text-sm font-medium @3xl/main:col-span-1">
          Open AMD mirrors <ArrowRight class="size-4" />
        </span>
      </button>
    </CardContent>
  </Card>
</template>
