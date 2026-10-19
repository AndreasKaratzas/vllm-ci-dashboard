<script setup lang="ts">
import { computed } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import {
  amdGroupObservations,
  amdGroupPassRate,
  amdLatestState,
  amdStateLabel,
  isIncidentObservation,
  observationState,
  observationTimestamp,
} from '@/domain/ciHealth'
import { integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import { exactPipelineEvidenceUrl } from '@/domain/urls'

const props = defineProps<{ row: Rec | null; latestBuild: unknown }>()
const open = defineModel<boolean>('open', { required: true })

const observations = computed(() => amdGroupObservations(props.row))
const incidents = computed(() => observations.value.filter(isIncidentObservation))
const latest = computed(() => (props.row ? amdLatestState(props.row, props.latestBuild) : 'unknown'))
const passRate = computed(() => amdGroupPassRate(props.row))
const stats = computed(() => {
  const row = props.row ?? {}
  return [
    {
      label: 'Latest AMD result',
      value: amdStateLabel(latest.value),
      meta: row.latest_build_number ? `#${row.latest_build_number} - ${shortDate(row.latest_observed_at)}` : 'No retained latest result',
    },
    {
      label: 'Retained-run pass rate',
      value: passRate.value === null ? '-' : `${passRate.value.toFixed(1)}%`,
      meta: `${integer(row.passed)} passed - ${integer(row.soft_failed)} soft - ${integer(row.hard_failed)} hard`,
    },
    { label: 'Current pass streak', value: integer(row.current_pass_streak), meta: `${integer(row.runs)} retained AMD nightlies` },
    {
      label: 'Non-passing runs',
      value: integer(incidents.value.length),
      meta: incidents.value.length ? 'Select a red or amber run for its Buildkite job' : 'None in retained history',
    },
  ]
})

function cellClass(observation: Rec) {
  const state = observationState(observation)
  if (state === 'passed') return 'bg-success'
  if (['soft', 'soft_fail', 'soft_failed'].includes(state)) return 'bg-warning'
  if (isIncidentObservation(observation)) return 'bg-destructive'
  return 'bg-muted-foreground/40'
}
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-3xl">
      <SheetHeader>
        <SheetTitle>{{ row?.display_name || row?.name || row?.job_name }}</SheetTitle>
        <SheetDescription>
          {{ value(row?.hardware_variant || row?.hardware) }} - {{ value(row?.queue) }} - exact main CI AMD gating-job evidence
        </SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-5 px-4 pb-6">
        <div class="grid grid-cols-2 gap-3">
          <div v-for="stat in stats" :key="stat.label" class="rounded-lg border p-3">
            <p class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ stat.label }}</p>
            <p class="text-lg font-semibold">
              <ToneBadge v-if="stat.label === 'Latest AMD result'" :tone="toneForState(latest)">{{ stat.value }}</ToneBadge>
              <template v-else>{{ stat.value }}</template>
            </p>
            <p class="text-xs text-muted-foreground">{{ stat.meta }}</p>
          </div>
        </div>
        <section>
          <h3 class="text-sm font-semibold">Main CI AMD gating-job outcomes</h3>
          <p class="mb-2 text-xs text-muted-foreground">{{ integer(observations.length) }} exact job runs, oldest to newest</p>
          <div class="flex flex-wrap gap-1" data-testid="history-cells">
            <a
              v-for="(observation, index) in observations"
              :key="index"
              :href="exactPipelineEvidenceUrl(observation, 'ci') || undefined"
              target="_blank"
              rel="noopener"
              class="size-4 rounded-sm"
              :class="cellClass(observation)"
              :title="`${observationState(observation)} · ${shortDate(observationTimestamp(observation))}`"
            />
          </div>
        </section>
        <section v-if="incidents.length">
          <h3 class="mb-2 text-sm font-semibold">Failure evidence · {{ integer(incidents.length) }} exact AMD jobs</h3>
          <ul class="divide-y rounded-lg border text-sm">
            <li v-for="(observation, index) in incidents.slice().reverse()" :key="index" class="flex items-center justify-between gap-3 px-3 py-2">
              <ToneBadge :tone="toneForState(observationState(observation))">{{ observationState(observation) }}</ToneBadge>
              <span class="text-muted-foreground">{{ shortDate(observationTimestamp(observation)) }}</span>
              <a
                v-if="exactPipelineEvidenceUrl(observation, 'ci')"
                :href="exactPipelineEvidenceUrl(observation, 'ci')"
                target="_blank"
                rel="noopener"
                class="font-medium hover:underline"
                >Open job ↗</a
              >
            </li>
          </ul>
        </section>
      </div>
    </SheetContent>
  </Sheet>
</template>
