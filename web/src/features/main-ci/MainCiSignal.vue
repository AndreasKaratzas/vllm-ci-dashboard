<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import { Button } from '@/components/ui/button'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { counted, integer, rateText } from '@/domain/format'
import { buildOutcome, buildTone, dayRateStatus } from '@/domain/mainCi'
import { useMainCi } from './useMainCi'

// Main CI is the source of truth; pages built from the nightly lead with its headline and link to it.
const mainCi = useMainCi()
const summary = computed(() => (mainCi.summary.value?.available ? mainCi.summary.value : null))
const latest = computed(() => mainCi.builds.value[0] ?? summary.value?.latest ?? null)
const rate = computed(() => summary.value?.last24h?.passRate ?? null)
const rateBadge = computed(() => dayRateStatus(summary.value?.last24h))
const active = computed(() => summary.value?.activeIncidents ?? 0)
const hard = computed(() => summary.value?.hardIncidents ?? 0)
</script>

<template>
  <Card v-if="summary" class="gap-3 py-4" data-testid="main-ci-signal">
    <CardHeader class="px-4 sm:px-6">
      <CardTitle class="text-base">Main CI</CardTitle>
      <CardDescription>Every AMD MI build on main, the source of truth; the nightly below is one build a day within it</CardDescription>
      <CardAction>
        <Button variant="outline" size="sm" as-child>
          <RouterLink :to="{ name: 'main' }">Open Main CI <ChevronRight /></RouterLink>
        </Button>
      </CardAction>
    </CardHeader>
    <CardContent class="grid grid-cols-1 gap-3 px-4 text-sm sm:grid-cols-3 sm:px-6">
      <div class="flex flex-col gap-1">
        <span class="text-xs text-muted-foreground">AMD pass rate, 24h</span>
        <span class="flex flex-wrap items-center gap-2">
          <span class="text-xl font-semibold tabular-nums" :class="rate === null && 'text-muted-foreground'">{{ rateText(rate) }}</span>
          <StatusBadge v-if="rateBadge" :tone="rateBadge.tone" :label="rateBadge.label" />
        </span>
      </div>
      <div class="flex flex-col gap-1">
        <span class="text-xs text-muted-foreground">Active main incidents</span>
        <span class="flex flex-wrap items-center gap-2">
          <span class="text-xl font-semibold tabular-nums" :class="!active && 'text-muted-foreground'">{{ integer(active) }}</span>
          <StatusBadge v-if="active" :tone="hard ? 'hard' : 'soft'" :label="hard ? `${integer(hard)} hard` : 'Soft'" />
        </span>
      </div>
      <div v-if="latest" class="flex flex-col gap-1">
        <span class="text-xs text-muted-foreground">Latest main build</span>
        <span class="flex flex-wrap items-center gap-2">
          <span class="font-mono text-xl font-semibold">#{{ latest.number }}</span>
          <StatusBadge :tone="buildTone(latest)" :label="buildOutcome(latest)" />
        </span>
        <span v-if="active" class="text-xs text-muted-foreground">{{ counted(active, 'incident') }} stay open until their groups pass again</span>
      </div>
    </CardContent>
  </Card>
</template>
