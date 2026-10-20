<script setup lang="ts">
import { ChevronRight, ExternalLink } from '@lucide/vue'
import { computed, watch } from 'vue'
import { RouterLink, useRouter } from 'vue-router'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import KpiCard from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { useClock } from '@/composables/useClock'
import { useOperationSections } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { integer, reasonText, toneForState, type Rec } from '@/domain/format'
import { areaSources } from '@/domain/freshness'
import { HOME_EXCLUDED_ATTENTION, attentionLabel, currentAmdHealth, currentTestGroupParity } from '@/domain/health'
import { attentionTone, nightlySeries, passRateTrend } from '@/domain/home'
import { movementBuilds } from '@/domain/ciHealth'
import { buildsBetween, type TrendPoint } from '@/domain/mainCi'
import { amdNightlyPresentation, ciHealthPublicationRetentionMessage, latestAmd, nightlyCountLabel } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'
import { useFreshness } from '@/features/freshness/useFreshness'
import { statusVar } from '@/lib/palette'
import { buildEvidenceDetail } from '@/features/nightly/buildEvidence'
import NightlyMovementChart from '@/features/nightly/NightlyMovementChart.vue'
import { trendPointDetail } from '@/features/main-ci/details'
import MainCiKpis from '@/features/main-ci/MainCiKpis.vue'
import MainCiTrendCard from '@/features/main-ci/MainCiTrendCard.vue'
import { useMainCi } from '@/features/main-ci/useMainCi'
import AttentionCard from './home/AttentionCard.vue'
import NightlyTable from './home/NightlyTable.vue'
import NightlyTrendCard from './home/NightlyTrendCard.vue'
import WorkbenchCard from './home/WorkbenchCard.vue'

// Collector inputs of the KPIs and charts; the workbench's project board is the 'home' area source.
const HOME_INPUTS = ['main_ci', 'analytics', 'ci_health', 'test_group_parity'] as const

const router = useRouter()
const now = useClock()
const { data: ops, error, loading, retry } = useOperationSections(['nightly'])
const sheets = useSheetStack()
const mainCi = useMainCi()
const mainSummary = computed(() => (mainCi.published.value && mainCi.summary.value?.available ? mainCi.summary.value : null))
const latestMain = computed(() => mainCi.builds.value[0] ?? mainSummary.value?.latest ?? null)
function openMainPoint(point: TrendPoint, label: string) {
  sheets.open(trendPointDetail(point, buildsBetween(mainCi.builds.value, point.from, point.to), label, mainCi.ctx.value))
}

const amd = computed(() => latestAmd(ops.value))
const builds = computed<Rec[]>(() => amd.value.builds || [])
const latestBuild = computed<Rec>(() => builds.value[0] || {})
const latestUrl = computed(() => exactPipelineBuildUrl(latestBuild.value, 'ci'))
const nightly = computed(() =>
  amdNightlyPresentation(latestBuild.value, currentAmdHealth(ops.value?.amd_test_health).summary || {}, ops.value?.generated_at, now.value),
)
const series = computed(() => nightlySeries(builds.value))
const passRate = computed(() => passRateTrend(builds.value))
const parity = computed<Rec>(() => currentTestGroupParity(ops.value?.test_group_parity).summary || {})
const retentionMessage = computed(() => ciHealthPublicationRetentionMessage(amd.value))
const attention = computed<Rec[]>(() =>
  (ops.value?.attention || []).filter((item: Rec) => !HOME_EXCLUDED_ATTENTION.includes(item.kind)),
)
const freshness = useFreshness().chip([...HOME_INPUTS, ...areaSources('home')], 'Home data')
// The headline count carries the outcome; the footnote keeps the build's counts and Buildkite state.
const nightlyFootnote = computed(() => nightly.value.meta.replace(`; ${nightly.value.movementLabel}`, ''))

const parityAvailable = computed(() => parity.value.main_complete_groups !== undefined && Number(parity.value.applicable_groups) > 0)
const parityRate = computed(() => (Number(parity.value.main_complete_groups) / Number(parity.value.applicable_groups)) * 100)
const parityMissing = computed(() => Number(parity.value.main_missing_groups || 0))
const paritySegments = computed(() => [
  { key: 'covered', label: 'Covered', count: Number(parity.value.main_complete_groups || 0), color: statusVar('pass') },
  { key: 'missing', label: 'Missing', count: parityMissing.value, color: statusVar('soft') },
])
const attentionSegments = computed(() =>
  (['hard', 'soft'] as const).map((tone) => ({
    key: tone,
    label: tone === 'hard' ? 'Critical' : 'Warning',
    count: attention.value.filter((item) => toneForState(item.severity) === tone).length,
    color: statusVar(tone),
  })),
)
const breakdown = (segments: { label: string; count: number }[]) =>
  segments
    .filter((segment) => segment.count)
    .map((segment) => `${integer(segment.count)} ${segment.label.toLowerCase()}`)
    .join(' · ')
const pointsText = (delta: number) => `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts`

function openBuild(build: Rec, scope?: string, title?: string) {
  sheets.open(buildEvidenceDetail(build, { scope, title }))
}
function openBuildNumber(number: number) {
  const build = builds.value.find((b) => Number(b.number) === number)
  if (build) openBuild(build)
}
function inspectAttention(item: Rec) {
  if (item.kind === 'amd_logical_groups_not_fully_passing') void router.push({ name: 'health' })
  else if (item.kind === 'omni_waiting') void router.push({ name: 'omni' })
  else if (latestBuild.value.number) {
    const scope = item.kind === 'nightly_hard_failures' ? 'hard' : item.kind === 'nightly_soft_failures' ? 'soft' : undefined
    openBuild(latestBuild.value, scope, attentionLabel(item))
  }
}
function showAttention() {
  const list = document.getElementById('needs-attention')
  list?.scrollIntoView({ behavior: 'smooth', block: 'start' })
  list?.querySelector<HTMLElement>('button')?.focus({ preventScroll: true })
}

// ?build=<number> (from the command palette) opens that nightly's evidence; closing it clears the parameter.
const buildParam = useQueryState('build', { default: '' })
watch(
  [buildParam, builds],
  ([number]) => {
    if (number && !sheets.stack.value.length) openBuildNumber(Number(number))
  },
  { immediate: true },
)
watch(sheets.stack, (stack) => {
  if (!stack.length && buildParam.value) buildParam.value = ''
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader
      title="Home"
      description="AMD CI health on vLLM main first, then the nightly, upstream parity, and open ROCm CI work."
      :freshness="freshness"
    >
      <template #about>
        <p>
          <strong class="text-foreground">Main CI</strong> is every completed AMD MI build on vLLM main, the source of truth; its pass rate counts the final AMD job
          attempt per test group, and incidents are failures the main-failure watcher confirmed.
        </p>
        <p><strong class="text-foreground">Nightly pass rate</strong> is the share of job variants that passed in a nightly with parsed test results; nightlies without a test signal are left out of the trend.</p>
        <p>
          <strong class="text-foreground">Failure movement</strong> compares each nightly with the preceding eligible one: every current hard or soft failure is
          new or recurring, and a previous failure that now passes is fixed. Missing or skipped jobs are omitted.
        </p>
        <p><strong class="text-foreground">Upstream parity</strong> is the share of applicable upstream test groups with an AMD route on main CI.</p>
        <p>Queue signals are not listed here; they live on Omni CI and CI Analytics.</p>
      </template>
      <template #actions>
        <Button v-if="latestMain?.url" variant="outline" size="sm" class="h-8" as="a" :href="latestMain.url" target="_blank" rel="noopener">
          Main #{{ latestMain.number }} <ExternalLink />
        </Button>
        <Button v-if="latestUrl" variant="outline" size="sm" class="h-8" as="a" :href="latestUrl" target="_blank" rel="noopener">
          Nightly #{{ latestBuild.number }} <ExternalLink />
        </Button>
      </template>
    </PageHeader>

    <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
      <div class="flex flex-col gap-4 md:gap-6">
        <section class="flex flex-col gap-4 md:gap-6" aria-labelledby="home-main-heading" data-testid="home-main">
          <h2 id="home-main-heading" class="sr-only">Main CI</h2>
          <template v-if="mainSummary">
            <MainCiKpis
              :summary="mainSummary"
              :record="mainCi.ctx.value.record ?? mainCi.record.value"
              :builds="mainCi.builds.value"
              :actions="{
                latest: latestMain ? { label: 'Open build evidence', to: { name: 'main', query: { build: String(latestMain.number) } } } : undefined,
                rate: { label: 'Open main CI', to: { name: 'main' } },
                incidents: { label: 'Open incidents', to: { name: 'main', query: { view: 'incidents' } } },
                flaky: { label: 'Open flaky groups', to: { name: 'main', query: { view: 'flaky' } } },
              }"
            />
            <MainCiTrendCard v-if="mainCi.loaded.value" :record="mainCi.ctx.value.record" @select="openMainPoint" />
            <AsyncBoundary v-else :loading="!mainCi.error.value" :error="mainCi.error.value" skeleton="chart" @retry="mainCi.retry" />
          </template>
          <Callout v-else-if="mainCi.summary.value && !mainCi.summary.value.available" tone="soft" title="Main CI data is unavailable" data-testid="home-main-unavailable">
            {{ reasonText(mainCi.summary.value.reason) }}. Nightly signals below remain current.
          </Callout>
        </section>

        <div class="flex flex-wrap items-end justify-between gap-2 pt-2" data-testid="home-nightly-heading">
          <div class="flex flex-col gap-1">
            <h2 class="text-lg font-semibold tracking-tight">AMD nightly</h2>
            <p class="text-sm text-muted-foreground">The scheduled full run once a day on main, with failure movement against the previous nightly.</p>
          </div>
          <Button variant="ghost" size="sm" as-child>
            <RouterLink :to="{ name: 'analytics', query: { view: 'nightlies' } }">All nightlies <ChevronRight /></RouterLink>
          </Button>
        </div>
        <Callout v-if="retentionMessage" tone="soft" title="Partial history">{{ retentionMessage }}</Callout>

        <StatGrid>
          <KpiCard
            label="Latest AMD nightly"
            :value="nightly.hasSignal ? nightly.incidentCount : null"
            unit="failing"
            :delta="nightly.incidentDelta !== null ? { value: nightly.incidentDelta, goodWhen: 'down', text: nightly.movementLabel } : undefined"
            :status="nightly.incidentDelta === null ? { tone: nightly.tone, label: nightly.label } : undefined"
            :spark="series.map((row) => row.hard + row.soft)"
            :headline="nightly.label"
            :footnote="nightlyFootnote"
            :action="latestUrl ? { label: `Open #${latestBuild.number} on Buildkite`, href: latestUrl } : undefined"
            data-testid="kpi-nightly"
          />
          <KpiCard
            v-if="passRate"
            label="Latest pass rate"
            :value="passRate.latest.passRate * 100"
            :format="(value: number) => `${value.toFixed(1)}%`"
            :delta="passRate.deltaPoints !== null ? { value: Number(passRate.deltaPoints.toFixed(1)), goodWhen: 'up', text: pointsText(passRate.deltaPoints) } : undefined"
            :spark="series.map((row) => row.passRate * 100)"
            headline="Open nightly evidence"
            :footnote="`${integer(passRate.latest.passed)} of ${integer(passRate.latest.total)} job variants passed in #${passRate.latest.number}`"
            :action="{ label: 'Open nightly evidence', onOpen: () => openBuildNumber(passRate!.latest.number) }"
            data-testid="kpi-pass-rate"
          />
          <EmptyState v-else title="No pass rate yet" description="The latest nightly has no parsed test groups." class="py-6" data-testid="kpi-pass-rate" />
          <KpiCard
            v-if="parityAvailable"
            label="Upstream test-group parity"
            :value="parityRate"
            :format="(value: number) => `${value.toFixed(1)}%`"
            :status="{ tone: Number(parity.action_groups) ? 'soft' : 'pass', label: `${integer(parityMissing)} missing` }"
            headline="Open upstream parity"
            :footnote="`${integer(parity.main_complete_groups)} / ${integer(parity.applicable_groups)} upstream test groups covered on main`"
            :action="{ label: 'Open upstream parity', to: { name: 'health', query: { view: 'parity' } } }"
            data-testid="kpi-parity"
          >
            <template #visual>
              <ProportionBar :segments="paritySegments" :max="Number(parity.applicable_groups)" label="Upstream test groups on main" :legend="false" />
              <p class="mt-1.5 text-xs text-muted-foreground tabular-nums">{{ breakdown(paritySegments) }}</p>
            </template>
          </KpiCard>
          <EmptyState v-else title="Parity unavailable" description="The current main CI parity inventory is not published." class="py-6" data-testid="kpi-parity" />
          <KpiCard
            label="Needs attention"
            :value="attention.length"
            :unit="attention.length === 1 ? 'signal' : 'signals'"
            :status="attention.length ? { tone: attentionTone(attention), label: 'Active' } : undefined"
            :headline="attention.length ? 'Review active signals' : undefined"
            :footnote="attention.length ? attentionLabel(attention[0]!) : 'No active AMD nightly or route signals'"
            :action="attention.length ? { label: 'Review active signals', onOpen: showAttention } : undefined"
            data-testid="kpi-attention"
          >
            <template v-if="attention.length" #visual>
              <ProportionBar :segments="attentionSegments" label="Active signals by severity" :legend="false" />
              <p class="mt-1.5 text-xs text-muted-foreground tabular-nums">{{ breakdown(attentionSegments) }}</p>
            </template>
          </KpiCard>
        </StatGrid>

        <div class="grid grid-cols-1 items-start gap-4 md:gap-6 @5xl/main:grid-cols-2 *:min-w-0">
          <NightlyTrendCard :series="series" @select="openBuildNumber" />
          <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300">
            <CardHeader>
              <CardTitle class="text-base">Failure movement</CardTitle>
              <CardDescription>New and recurring failures above zero, fixes below · {{ nightlyCountLabel(movementBuilds(builds).length, 'builds') }}</CardDescription>
            </CardHeader>
            <CardContent class="px-3 sm:px-6">
              <NightlyMovementChart :builds="movementBuilds(builds)" label="AMD nightly failure movement" :on-select="(build: Rec) => openBuild(build)" />
            </CardContent>
          </Card>
        </div>

        <AttentionCard :items="attention" @inspect="inspectAttention" />
        <NightlyTable :builds="builds" @open="openBuild" />
        <WorkbenchCard />
      </div>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
