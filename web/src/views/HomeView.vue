<script setup lang="ts">
import type { ColumnDef } from '@tanstack/vue-table'
import { computed, h, ref } from 'vue'
import { useRouter } from 'vue-router'
import BuildDetailSheet from '@/components/ops/BuildDetailSheet.vue'
import DataTable from '@/components/ops/DataTable.vue'
import KpiCard from '@/components/ops/KpiCard.vue'
import NightlyChart from '@/components/ops/NightlyChart.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useOperations, useWorkItems } from '@/composables/useOperations'
import { integer, observedCountLabel, percent, shortDate, toneForState, type Rec, type Tone } from '@/domain/format'
import { HOME_EXCLUDED_ATTENTION, attentionLabel, currentAmdHealth, currentTestGroupParity } from '@/domain/health'
import { nightlySeries, passRateTrend } from '@/domain/home'
import {
  amdNightlyPresentation,
  ciHealthPublicationRetentionMessage,
  latestAmd,
  nightlyFailureCount,
  type MovementKey,
} from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'

const router = useRouter()
const { ops, error, loading, retry } = useOperations()
const work = useWorkItems()

const amd = computed(() => latestAmd(ops.value))
const builds = computed<Rec[]>(() => amd.value.builds || [])
const latestBuild = computed<Rec>(() => builds.value[0] || {})
const nightly = computed(() =>
  amdNightlyPresentation(latestBuild.value, currentAmdHealth(ops.value?.amd_test_health).summary || {}, ops.value?.generated_at),
)
const series = computed(() => nightlySeries(builds.value))
const passRate = computed(() => passRateTrend(builds.value))
const parity = computed<Rec>(() => currentTestGroupParity(ops.value?.test_group_parity).summary || {})
const retentionMessage = computed(() => ciHealthPublicationRetentionMessage(amd.value))
const attention = computed<Rec[]>(() =>
  (ops.value?.attention || []).filter((item: Rec) => !HOME_EXCLUDED_ATTENTION.includes(item.kind)),
)
const recentBuilds = computed(() => builds.value.slice(0, 7))

const nightlyTrend = computed<'up' | 'down' | null>(() =>
  nightly.value.incidentDelta === null || nightly.value.incidentDelta === 0 ? null : nightly.value.incidentDelta > 0 ? 'up' : 'down',
)
const passRateCard = computed(() => {
  const trend = passRate.value
  if (!trend) return { value: 'No signal', badge: undefined, tone: 'is-neutral' as Tone, trend: null, detail: 'Latest nightly has no parsed test groups' }
  const delta = trend.deltaPoints
  return {
    value: percent(trend.latest.passed, trend.latest.total),
    badge: delta === null ? 'first signal' : `${delta >= 0 ? '+' : ''}${delta.toFixed(1)} pts`,
    tone: (delta === null || Math.abs(delta) < 0.05 ? 'is-neutral' : delta > 0 ? 'is-success' : 'is-danger') as Tone,
    trend: delta === null || Math.abs(delta) < 0.05 ? null : delta > 0 ? ('up' as const) : ('down' as const),
    detail: `${integer(trend.latest.passed)} of ${integer(trend.latest.total)} job variants passed in #${trend.latest.number}`,
  }
})
const parityCard = computed(() => {
  const p = parity.value
  const available = p.main_complete_groups !== undefined
  return {
    value: available ? percent(p.main_complete_groups, p.applicable_groups) : 'Unavailable',
    badge: available ? `${integer(p.main_missing_groups)} missing` : undefined,
    tone: (Number(p.action_groups) ? 'is-warning' : 'is-success') as Tone,
    detail: available
      ? `${integer(p.main_complete_groups)} / ${integer(p.applicable_groups)} upstream test groups covered on main`
      : 'Current main CI parity inventory unavailable',
  }
})
const attentionTone = computed<Tone>(() => {
  const tones = attention.value.map((item) => toneForState(item.severity))
  return tones.includes('is-danger') ? 'is-danger' : tones.includes('is-warning') ? 'is-warning' : 'is-success'
})

const workKind = ref<'issues' | 'prs'>('issues')
const workRows = computed<Rec[]>(() => (workKind.value === 'prs' ? work.value?.prs : work.value?.issues) ?? [])
const workColumns = computed<ColumnDef<Rec, unknown>[]>(() => [
  {
    id: 'title',
    header: workKind.value === 'prs' ? 'Pull request' : 'Issue',
    accessorFn: (row) => `#${row.number} ${row.title}`,
    cell: ({ row }) =>
      h('a', { href: row.original.html_url, target: '_blank', rel: 'noopener', class: 'font-medium hover:underline' }, `#${row.original.number} ${row.original.title}`),
    enableHiding: false,
  },
  {
    id: 'assignee',
    header: 'Assignee',
    accessorFn: (row) => row.author || (row.assignees || [])[0] || '',
    cell: ({ getValue }) => {
      const who = String(getValue() || '')
      return who
        ? h('a', { href: `https://github.com/${encodeURIComponent(who)}`, target: '_blank', rel: 'noopener', class: 'hover:underline' }, who)
        : h('span', { class: 'text-muted-foreground' }, 'Unassigned')
    },
  },
  {
    id: 'state',
    header: 'State',
    accessorFn: (row) => (row.merged ? 'merged' : row.state),
    cell: ({ getValue }) => h(ToneBadge, { tone: toneForState(getValue()) }, () => String(getValue())),
  },
  {
    id: 'labels',
    header: 'Labels',
    accessorFn: (row) => (row.custom_tags || row.labels || []).slice(0, 4).join(', '),
    cell: ({ getValue }) => h('span', { class: 'text-muted-foreground' }, String(getValue() || '-')),
    enableSorting: false,
  },
  {
    id: 'updated',
    header: 'Updated',
    accessorFn: (row) => row.updated_at || '',
    cell: ({ getValue }) => h('span', { class: 'text-muted-foreground whitespace-nowrap' }, shortDate(getValue())),
  },
])

const detail = ref<{ open: boolean; build: Rec | null; title?: string; scope?: string }>({ open: false, build: null })
function openBuild(build: Rec, title?: string, scope?: string) {
  detail.value = { open: true, build, title, scope }
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
    openBuild(latestBuild.value, attentionLabel(item), scope)
  }
}
function testSignal(build: Rec) {
  if (build.has_test_results !== false) return { label: 'Observed', tone: 'is-success' as const }
  return { label: Number(build.test_jobs_blocked || 0) ? 'Infra blocked' : 'Unavailable', tone: 'is-danger' as const }
}
function movementCount(build: Rec, key: MovementKey) {
  const count = nightlyFailureCount(build, key)
  return count === null ? '-' : integer(count)
}
</script>

<template>
  <ViewState :loading="loading && !ops" :error="error" @retry="retry">
    <div class="flex flex-col gap-4 md:gap-6">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight">Command Center</h2>
        <p class="text-sm text-muted-foreground">
          Current AMD operations with observed nightly failure movement and direct paths to source evidence.
        </p>
      </div>
      <p v-if="retentionMessage" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
        {{ retentionMessage }}
      </p>

      <div
        class="grid grid-cols-1 gap-4 *:data-[slot=card]:bg-gradient-to-t *:data-[slot=card]:from-primary/5 *:data-[slot=card]:to-card *:data-[slot=card]:shadow-xs @xl/main:grid-cols-2 @5xl/main:grid-cols-4 dark:*:data-[slot=card]:bg-card"
      >
        <KpiCard
          label="Latest AMD nightly"
          :value="nightly.label"
          :badge="nightly.movementLabel"
          :badge-tone="nightly.movementTone"
          :trend="nightlyTrend"
          :headline="latestBuild.number ? `Open #${latestBuild.number} on Buildkite` : undefined"
          :href="exactPipelineBuildUrl(latestBuild, 'ci') || undefined"
          :detail="nightly.meta"
          data-testid="kpi-nightly"
        />
        <KpiCard
          label="Latest pass rate"
          :value="passRateCard.value"
          :badge="passRateCard.badge"
          :badge-tone="passRateCard.tone"
          :trend="passRateCard.trend"
          headline="Open nightly evidence"
          :detail="passRateCard.detail"
          @open="passRate && openBuildNumber(passRate.latest.number)"
        />
        <KpiCard
          label="Upstream test-group parity"
          :value="parityCard.value"
          :badge="parityCard.badge"
          :badge-tone="parityCard.tone"
          headline="Open Upstream parity →"
          :detail="parityCard.detail"
          @open="router.push({ name: 'health' })"
        />
        <KpiCard
          label="Needs attention"
          :value="`${attention.length} signals`"
          :badge="attention.length ? 'active' : 'clear'"
          :badge-tone="attentionTone"
          :detail="attention.length ? attention.map(attentionLabel)[0] : 'No active AMD nightly or route signals'"
        />
      </div>

      <NightlyChart :series="series" @select="openBuildNumber" />

      <div class="grid gap-4 md:gap-6 @5xl/main:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Needs attention</CardTitle>
            <CardDescription>{{ attention.length }} active signals</CardDescription>
          </CardHeader>
          <CardContent>
            <div class="overflow-hidden rounded-lg border">
              <Table>
                <TableHeader class="bg-muted">
                  <TableRow>
                    <TableHead>Operational signal</TableHead>
                    <TableHead>Severity</TableHead>
                    <TableHead class="text-right">Count</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow v-for="item in attention" :key="item.kind" class="cursor-pointer" data-testid="attention-row" @click="inspectAttention(item)">
                    <TableCell class="font-medium whitespace-normal">{{ attentionLabel(item) }}</TableCell>
                    <TableCell><ToneBadge :tone="toneForState(item.severity)">{{ item.severity }}</ToneBadge></TableCell>
                    <TableCell class="text-right tabular-nums">{{ integer(item.count) }}</TableCell>
                  </TableRow>
                  <TableRow v-if="!attention.length">
                    <TableCell colspan="3" class="h-20 text-center text-muted-foreground">No active signals.</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AMD nightly failure movement</CardTitle>
            <CardDescription>Latest seven completed observations · counts open their job variants</CardDescription>
          </CardHeader>
          <CardContent>
            <div class="overflow-x-auto rounded-lg border">
              <Table>
                <TableHeader class="bg-muted">
                  <TableRow>
                    <TableHead>Build</TableHead>
                    <TableHead>Test signal</TableHead>
                    <TableHead class="text-right">New</TableHead>
                    <TableHead class="text-right">Recurring</TableHead>
                    <TableHead class="text-right">Fixed</TableHead>
                    <TableHead>Observed</TableHead>
                  </TableRow>
                </TableHeader>
                <TableBody>
                  <TableRow v-for="build in recentBuilds" :key="build.number" data-testid="nightly-row">
                    <TableCell class="font-mono">
                      <a :href="exactPipelineBuildUrl(build, 'ci') || undefined" target="_blank" rel="noopener" class="hover:underline">#{{ build.number }}</a>
                    </TableCell>
                    <TableCell>
                      <button type="button" @click="openBuild(build)">
                        <ToneBadge :tone="testSignal(build).tone">{{ testSignal(build).label }}</ToneBadge>
                      </button>
                    </TableCell>
                    <TableCell v-for="key in ['new', 'recurring', 'fixed'] as const" :key="key" class="text-right tabular-nums">
                      <button type="button" class="hover:underline" @click="openBuild(build, undefined, key)">{{ movementCount(build, key) }}</button>
                    </TableCell>
                    <TableCell class="whitespace-nowrap text-muted-foreground">{{ shortDate(build.created_at) }}</TableCell>
                  </TableRow>
                </TableBody>
              </Table>
            </div>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader class="flex flex-row flex-wrap items-center justify-between gap-2">
          <div>
            <CardTitle>Engineering workbench</CardTitle>
            <CardDescription>Open ROCm CI issues and pull requests</CardDescription>
          </div>
          <Tabs v-model="workKind">
            <TabsList>
              <TabsTrigger value="issues">Issues ({{ observedCountLabel(work?.issues.length ?? 0, work?.issuesSemantics ?? 'lower_bound') }})</TabsTrigger>
              <TabsTrigger value="prs">PRs ({{ observedCountLabel(work?.prs.length ?? 0, work?.prsSemantics ?? 'lower_bound') }})</TabsTrigger>
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>
          <DataTable :key="workKind" :columns="workColumns" :data="workRows" search-placeholder="Filter title, assignee, label…" />
        </CardContent>
      </Card>
    </div>
    <BuildDetailSheet v-model:open="detail.open" :build="detail.build" :title="detail.title" :scope="detail.scope" />
  </ViewState>
</template>
