<script setup lang="ts">
import { computed, ref } from 'vue'
import { useRouter } from 'vue-router'
import BuildDetailSheet from '@/components/ops/BuildDetailSheet.vue'
import PageHeader from '@/components/ops/PageHeader.vue'
import StatusCard from '@/components/ops/StatusCard.vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useAsync } from '@/composables/useAsync'
import { fetchJSON } from '@/data/fetch'
import { loadOperations } from '@/data/operations'
import {
  integer,
  observedCountLabel,
  percent,
  populationSemantics,
  shortDate,
  toneForState,
  type Rec,
} from '@/domain/format'
import {
  HOME_EXCLUDED_ATTENTION,
  attentionLabel,
  currentAmdHealth,
  currentTestGroupParity,
} from '@/domain/health'
import {
  amdNightlyPresentation,
  ciHealthPublicationRetentionMessage,
  latestAmd,
  nightlyFailureCount,
  type MovementKey,
} from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'

const router = useRouter()
const ops = useAsync(() => loadOperations([]))
const work = useAsync(async () => {
  try {
    const [prs, issues] = await Promise.all([fetchJSON<Rec>('data/vllm/prs.json'), fetchJSON<Rec>('data/vllm/issues.json')])
    return {
      prs: (prs.prs || []) as Rec[],
      issues: (issues.issues || []) as Rec[],
      prsSemantics: populationSemantics(prs),
      issuesSemantics: populationSemantics(issues),
    }
  } catch {
    // Project activity is secondary on Home; operations data still renders.
    return { prs: [], issues: [], prsSemantics: 'lower_bound', issuesSemantics: 'lower_bound' }
  }
})

const amd = computed(() => latestAmd(ops.data.value))
const builds = computed<Rec[]>(() => amd.value.builds || [])
const latestBuild = computed<Rec>(() => builds.value[0] || {})
const nightly = computed(() =>
  amdNightlyPresentation(
    latestBuild.value,
    currentAmdHealth(ops.data.value?.amd_test_health).summary || {},
    ops.data.value?.generated_at,
  ),
)
const parity = computed<Rec>(() => currentTestGroupParity(ops.data.value?.test_group_parity).summary || {})
const parityCard = computed(() => {
  const p = parity.value
  return {
    value:
      p.main_complete_groups === undefined
        ? 'Unavailable'
        : `${integer(p.main_complete_groups)} / ${integer(p.applicable_groups)} on main`,
    meta:
      p.upstream_logical_groups === undefined
        ? 'Current main CI parity inventory unavailable'
        : `${percent(p.main_complete_groups, p.applicable_groups)} · ${integer(p.main_missing_groups)} missing`,
    tone: Number(p.action_groups) ? ('is-warning' as const) : ('is-success' as const),
  }
})
const retentionMessage = computed(() => ciHealthPublicationRetentionMessage(amd.value))
const attention = computed<Rec[]>(() =>
  (ops.data.value?.attention || []).filter((item: Rec) => !HOME_EXCLUDED_ATTENTION.includes(item.kind)),
)
const recentBuilds = computed(() => builds.value.slice(0, 7))

const workKind = ref<'issues' | 'prs'>('issues')
const workRows = computed(() => (workKind.value === 'prs' ? work.data.value?.prs : work.data.value?.issues) ?? [])

const detail = ref<{ open: boolean; build: Rec | null; title?: string; scope?: string }>({ open: false, build: null })
function openBuild(build: Rec, title?: string, scope?: string) {
  detail.value = { open: true, build, title, scope }
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
  <ViewState :loading="ops.loading.value" :error="ops.error.value" @retry="ops.retry">
    <div class="flex flex-col gap-6">
      <PageHeader
        title="Command Center"
        description="Current AMD operations with observed nightly failure movement and direct paths to source evidence."
        :generated-at="ops.data.value?.generated_at"
      />
      <p v-if="retentionMessage" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">
        {{ retentionMessage }}
      </p>

      <div class="grid gap-4 md:grid-cols-2">
        <StatusCard
          label="Latest AMD nightly"
          :value="nightly.label"
          :meta="nightly.meta"
          :tone="nightly.tone"
          :href="exactPipelineBuildUrl(latestBuild, 'ci') || undefined"
          action-label="Open Buildkite ↗"
        />
        <StatusCard
          label="Upstream test-group parity"
          :value="parityCard.value"
          :meta="parityCard.meta"
          :tone="parityCard.tone"
          action-label="Open Upstream parity →"
          @open="router.push({ name: 'health' })"
        />
      </div>

      <div class="grid gap-4 lg:grid-cols-[minmax(0,1fr)_minmax(0,1.4fr)]">
        <Card>
          <CardHeader>
            <CardTitle>Needs attention</CardTitle>
            <CardDescription>{{ attention.length }} active signals</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Operational signal</TableHead>
                  <TableHead>Severity</TableHead>
                  <TableHead class="text-right">Count</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                <TableRow
                  v-for="item in attention"
                  :key="item.kind"
                  class="cursor-pointer"
                  data-testid="attention-row"
                  @click="inspectAttention(item)"
                >
                  <TableCell class="whitespace-normal font-medium">{{ attentionLabel(item) }}</TableCell>
                  <TableCell><ToneBadge :tone="toneForState(item.severity)">{{ item.severity }}</ToneBadge></TableCell>
                  <TableCell class="text-right tabular-nums">{{ integer(item.count) }}</TableCell>
                </TableRow>
                <TableRow v-if="!attention.length">
                  <TableCell colspan="3" class="text-muted-foreground">No active signals.</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>

        <Card>
          <CardHeader>
            <CardTitle>AMD nightly failure movement</CardTitle>
            <CardDescription>Latest seven completed observations</CardDescription>
          </CardHeader>
          <CardContent>
            <Table>
              <TableHeader>
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
                    <a :href="exactPipelineBuildUrl(build, 'ci') || undefined" target="_blank" rel="noopener" class="hover:underline">
                      #{{ build.number }}
                    </a>
                  </TableCell>
                  <TableCell>
                    <button type="button" @click="openBuild(build)">
                      <ToneBadge :tone="testSignal(build).tone">{{ testSignal(build).label }}</ToneBadge>
                    </button>
                  </TableCell>
                  <TableCell v-for="key in (['new', 'recurring', 'fixed'] as const)" :key="key" class="text-right tabular-nums">
                    <button type="button" class="hover:underline" @click="openBuild(build, undefined, key)">
                      {{ movementCount(build, key) }}
                    </button>
                  </TableCell>
                  <TableCell class="text-muted-foreground">{{ shortDate(build.created_at) }}</TableCell>
                </TableRow>
              </TableBody>
            </Table>
          </CardContent>
        </Card>
      </div>

      <Card>
        <CardHeader class="flex flex-row flex-wrap items-center justify-between gap-2">
          <CardTitle>Engineering workbench</CardTitle>
          <Tabs v-model="workKind">
            <TabsList>
              <TabsTrigger value="issues">
                Issues ({{ observedCountLabel(work.data.value?.issues.length ?? 0, work.data.value?.issuesSemantics ?? 'lower_bound') }})
              </TabsTrigger>
              <TabsTrigger value="prs">
                PRs ({{ observedCountLabel(work.data.value?.prs.length ?? 0, work.data.value?.prsSemantics ?? 'lower_bound') }})
              </TabsTrigger>
            </TabsList>
          </Tabs>
        </CardHeader>
        <CardContent>
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>{{ workKind === 'prs' ? 'Pull request' : 'Issue' }}</TableHead>
                <TableHead>Assignee</TableHead>
                <TableHead>State</TableHead>
                <TableHead>Labels</TableHead>
                <TableHead>Updated</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="row in workRows" :key="row.number">
                <TableCell class="max-w-xl whitespace-normal">
                  <a :href="row.html_url" target="_blank" rel="noopener" class="font-medium hover:underline">
                    #{{ row.number }} {{ row.title }}
                  </a>
                </TableCell>
                <TableCell>
                  <template v-if="row.author || (row.assignees || [])[0]">
                    <a
                      :href="`https://github.com/${encodeURIComponent(row.author || row.assignees[0])}`"
                      target="_blank"
                      rel="noopener"
                      class="hover:underline"
                      >{{ row.author || row.assignees[0] }}</a
                    >
                  </template>
                  <span v-else class="text-muted-foreground">Unassigned</span>
                </TableCell>
                <TableCell>
                  <ToneBadge :tone="toneForState(row.merged ? 'merged' : row.state)">{{ row.merged ? 'merged' : row.state }}</ToneBadge>
                </TableCell>
                <TableCell class="text-muted-foreground">{{ (row.custom_tags || row.labels || []).slice(0, 4).join(', ') || '-' }}</TableCell>
                <TableCell class="text-muted-foreground">{{ shortDate(row.updated_at) }}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </CardContent>
      </Card>
    </div>
    <BuildDetailSheet v-model:open="detail.open" :build="detail.build" :title="detail.title" :scope="detail.scope" />
  </ViewState>
</template>
