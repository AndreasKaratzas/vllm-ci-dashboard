<script setup lang="ts">
import { computed } from 'vue'
import { integer, shortDate, type Rec } from '@/domain/format'
import { flakinessThresholds, mainCohort } from '@/domain/mainCi'
import { RATE_THRESHOLDS } from '@/lib/palette'

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
    Jobs that never ran or have no verdict are left out. A rate passes at {{ RATE_THRESHOLDS.pass }}% or more, is soft below
    {{ RATE_THRESHOLDS.pass }}% and hard below {{ RATE_THRESHOLDS.soft }}%.
  </p>
  <p>
    <strong class="text-foreground">Incidents</strong> are kept by the main-failure watcher: a hard failure opens one at once, a soft failure after it repeats
    in a second build, and the group's next pass resolves it, so an incident stays open across builds that did not run its group. Each names its last
    good and first bad commit for bisecting; incidents that share a range likely share one culprit commit. An incident is new in its first 24 hours.
  </p>
  <p v-if="thresholds">
    <strong class="text-foreground">Flaky groups</strong> flip between pass and fail across builds and alert at {{ thresholds.minFlips }} flips within
    {{ thresholds.windowDays }} days. <strong class="text-foreground">Passes after a retry</strong> alert at {{ thresholds.minRecoveries }} passes after a
    retried hard failure in the same window. The flakiness watcher's issue lists the groups that cross either threshold.
  </p>
</template>
