<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed, watch } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import PageHeader, { type Freshness } from '@/components/layout/PageHeader.vue'
import { SUB_VIEWS, subViewIds } from '@/components/layout/navigation'
import SubNav from '@/components/layout/SubNav.vue'
import { Button } from '@/components/ui/button'
import { useClock } from '@/composables/useClock'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { age, integer, reasonText } from '@/domain/format'
import { mainCohort } from '@/domain/mainCi'
import { useFreshness } from '@/features/freshness/useFreshness'
import { incidentDetail, mainBuildDetail } from '@/features/main-ci/details'
import { useMainCi } from '@/features/main-ci/useMainCi'
import MainCiAbout from './main-ci/MainCiAbout.vue'
import MainCiFlaky from './main-ci/MainCiFlaky.vue'
import MainCiIncidents from './main-ci/MainCiIncidents.vue'
import MainCiOverview from './main-ci/MainCiOverview.vue'

type MainView = 'overview' | 'incidents' | 'flaky'
const DESCRIPTION: Record<MainView, string> = {
  overview: 'Every completed AMD MI build on vLLM main: the source of truth for AMD CI health, with the nightly as one build a day within it.',
  incidents: 'Confirmed AMD failures on main, the commits that bound them, and the issue the main-failure watcher keeps.',
  flaky: 'AMD groups whose outcome flips between builds on main, and groups that pass only after Buildkite retries them.',
}

const view = useQueryState<MainView>('view', { default: SUB_VIEWS.main.default as MainView, allowed: subViewIds<MainView>('main') })
const mainCi = useMainCi()
const { summary, incidents, ctx, loaded, published } = mainCi
const sheets = useSheetStack()
watch(view, () => sheets.close())
const now = useClock()

const cohort = computed(() => mainCohort(ctx.value.record))
const sourceChip = useFreshness().chip(['main_ci'], 'Collected')
const freshness = computed<Freshness | undefined>(() => {
  const chip = sourceChip.value
  const watcher = incidents.value.watcherLastRun
  return chip && watcher ? { ...chip, detail: `${chip.detail} The main-failure watcher last ran ${age(watcher, now.value)}.` } : chip
})

// ?build=<number> and ?incident=<group id> (from the command palette or a shared link) open their sheet.
const buildParam = useQueryState('build', { default: '' })
const incidentParam = useQueryState('incident', { default: '' })
watch(
  [buildParam, incidentParam, ctx],
  ([build, incident]) => {
    if (sheets.stack.value.length) return
    const row = build ? ctx.value.builds.find((b) => b.number === Number(build)) : undefined
    if (row) return sheets.open(mainBuildDetail(row, ctx.value))
    const all = [...incidents.value.active, ...incidents.value.pending]
    const match = incident ? all.find((i) => i.groupId === incident) : undefined
    if (match) sheets.open(incidentDetail(match, ctx.value, incidents.value.issue, now.value))
  },
  { immediate: true },
)
watch(sheets.stack, (stack) => {
  if (stack.length) return
  if (buildParam.value) buildParam.value = ''
  else if (incidentParam.value) incidentParam.value = ''
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader title="Main CI" :description="DESCRIPTION[view]" :freshness="freshness">
      <template #about>
        <MainCiAbout :record="ctx.record" />
      </template>
      <template #actions>
        <Button v-if="incidents.issue" variant="outline" size="sm" class="h-8" as="a" :href="incidents.issue.url" target="_blank" rel="noopener" data-testid="main-issue-link">
          Managed issue #{{ incidents.issue.number }} <ExternalLink />
        </Button>
        <Button variant="outline" size="sm" class="h-8" as="a" href="https://buildkite.com/vllm/ci/builds?branch=main" target="_blank" rel="noopener">
          Main on Buildkite <ExternalLink />
        </Button>
      </template>
    </PageHeader>
    <SubNav :items="SUB_VIEWS.main.items" :default-value="SUB_VIEWS.main.default" label="Main CI sections" />

    <EmptyState
      v-if="mainCi.ops.value && !published"
      title="Main CI is not published in this snapshot"
      description="The operations manifest predates the main CI section; it appears with the next publication."
      data-testid="main-unpublished"
    />
    <Callout v-else-if="summary && !summary.available" tone="soft" title="Main CI data is unavailable" data-testid="main-unavailable">
      {{ reasonText(summary.reason) }}. The page shows the main CI section again once the all-main build cohort validates.
    </Callout>
    <AsyncBoundary v-else :loading="!loaded && !mainCi.error.value" :error="mainCi.error.value" @retry="mainCi.retry">
      <div class="flex flex-col gap-4 md:gap-6">
        <Callout v-if="cohort.omitted" tone="soft" title="Partial build list">
          The section keeps the newest {{ integer(cohort.published) }} of {{ integer(cohort.builds) }} builds; counts and rates still cover all of them.
        </Callout>
        <MainCiOverview v-if="view === 'overview'" :main-ci="mainCi" :sheets="sheets" />
        <MainCiIncidents v-else-if="view === 'incidents'" :main-ci="mainCi" :sheets="sheets" />
        <MainCiFlaky v-else :main-ci="mainCi" :sheets="sheets" />
      </div>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
