<script setup lang="ts">
import { computed } from 'vue'
import { integer, shortDate, type Rec } from '@/domain/format'
import { flakinessThresholds, mainCohort } from '@/domain/mainCi'

const props = defineProps<{ record: Rec | null }>()
const cohort = computed(() => mainCohort(props.record))
const thresholds = computed(() => flakinessThresholds(props.record))
</script>

<template>
  <p>
    <strong class="text-foreground">Builds</strong> are every completed (passed or failed) build of the Buildkite <code>ci</code> pipeline on
    branch main<template v-if="cohort.builds !== null">: {{ integer(cohort.builds) }} in the last {{ cohort.windowDays ?? 30 }} days ({{ shortDate(cohort.from) }} to
    {{ shortDate(cohort.to) }}), {{ integer(cohort.nightlies) }} of them scheduled nightlies</template>. Main is the source of truth; the nightly is one build a day within it.
  </p>
  <p>
    <strong class="text-foreground">AMD pass rate</strong> counts the final AMD MI job attempt per test group in each build: passed / (passed + soft + hard).
    Jobs that never ran or have no verdict are left out. Pass at 99% or more, soft below 99%, hard below 95%.
  </p>
  <p>
    <strong class="text-foreground">Incidents</strong> are kept by the main-failure watcher: a hard failure opens one at once, a soft failure after it repeats
    in a second build, and a pass resolves it. Each names its last good and first bad commit for bisecting.
  </p>
  <p v-if="thresholds">
    <strong class="text-foreground">Flaky groups</strong> flip between pass and fail across builds; a group alerts at {{ thresholds.minFlips }} flips, or
    {{ thresholds.minRecoveries }} passes after a retried hard failure, within {{ thresholds.windowDays }} days.
  </p>
</template>
