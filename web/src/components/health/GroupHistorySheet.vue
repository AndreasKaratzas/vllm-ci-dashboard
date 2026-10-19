<script setup lang="ts">
// Classic openGroupDetail / openMixedOutcomeEvidence for a matrix test group:
// the aggregate drawer, the on-demand 30-day reliability load, and the exact
// outcome, completion, and queue-wait history once a unique ledger row exists.
import { VisAxis, VisLine, VisStackedBar, VisXYContainer } from '@unovis/vue'
import { computed, ref, shallowRef, watch } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { ChartContainer, ChartLegendContent, type ChartConfig } from '@/components/ui/chart'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { loadOperations } from '@/data/operations'
import { observationState, observationTimestamp } from '@/domain/ciHealth'
import { duration, integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import {
  evidenceResultLabel,
  filterEvidenceRows,
  groupDetailState,
  groupPublicationHistoryComplete,
  isNightlyObservation,
  mixedOutcomeNotice,
  mixedOutcomeObservations,
  mixedOutcomeSummary,
  observationDurationMinutes,
  observationWaitMinutes,
  outcomeChartCaption,
  outcomeSeries,
  trailingPassStats,
} from '@/domain/healthHardware'
import { exactPipelineEvidenceUrl } from '@/domain/urls'
import DetailFields from './DetailFields.vue'

const props = defineProps<{ group: Rec | null; ops: Rec | null }>()
const open = defineModel<boolean>('open', { required: true })

const expanded = shallowRef<Rec | null>(null)
const loadState = ref<'idle' | 'loading' | 'failed'>('idle')
const mode = ref<'main' | 'nightly'>('main')
const query = ref('')
const resultFilter = ref('all')
watch(
  () => props.group,
  () => {
    // Classic reloads the ledger per drawer; the expanded payload is not kept.
    expanded.value = null
    mode.value = 'main'
    query.value = ''
    resultFilter.value = 'all'
    loadState.value = 'idle'
  },
)

const state = computed(() => (props.group ? groupDetailState(props.group, expanded.value ?? props.ops) : null))

async function loadHistory() {
  loadState.value = 'loading'
  try {
    const loaded = await loadOperations(['reliability'])
    expanded.value = { ...(props.ops ?? {}), reliability: loaded.reliability }
    loadState.value = 'idle'
  } catch (error) {
    console.error('Reliability evidence load failed:', error)
    loadState.value = 'failed'
  }
}

const candidate = computed<Rec | null>(() => (state.value?.kind === 'evidence' ? state.value.row : null))
const sourcePipeline = computed(() => candidate.value?.source_pipeline || 'ci')
const complete = computed(() => groupPublicationHistoryComplete(candidate.value))
const allObservations = computed(() => (candidate.value ? mixedOutcomeObservations(candidate.value) : []))
const notice = computed(() => mixedOutcomeNotice(allObservations.value, complete.value, sourcePipeline.value))
const observations = computed(() => allObservations.value.filter((row) => mode.value === 'main' || isNightlyObservation(row)))
const summary = computed(() => mixedOutcomeSummary(observations.value, complete.value, mode.value))
const rolling = computed(() => trailingPassStats(observations.value, 10))
const evidenceRows = computed(() => filterEvidenceRows(observations.value, query.value, resultFilter.value))
const hasDuration = computed(() =>
  observations.value.some((row) => observationDurationMinutes(row) !== null || observationWaitMinutes(row) !== null),
)
const hasWait = computed(() => observations.value.some((row) => observationWaitMinutes(row) !== null))

const STAT_TEXT: Record<string, string> = {
  pass: 'text-success',
  hard: 'text-destructive',
  soft: 'text-warning',
  neutral: '',
}

const outcomeConfig = {
  passed: { label: 'Passed run', color: 'var(--success)' },
  soft: { label: 'Soft failure', color: 'var(--warning)' },
  hard: { label: 'Hard failure', color: 'var(--destructive)' },
  rate: { label: 'Trailing 10-run pass rate', color: 'var(--info)' },
} satisfies ChartConfig
const durationConfig = computed<ChartConfig>(() => ({
  completion: { label: 'Completion', color: 'var(--chart-2)' },
  ...(hasWait.value ? { wait: { label: 'Queue wait', color: 'var(--warning)' } } : {}),
}))
interface Point {
  index: number
  label: string
  passed: number | null
  soft: number | null
  hard: number | null
  rate: number | null
  completion: number | null
  wait: number | null
}
const points = computed<Point[]>(() =>
  observations.value.map((row, index) => {
    const rate = complete.value ? rolling.value[index]?.rate ?? null : null
    const series = outcomeSeries(row)
    return {
      index,
      label: row.build_number ? `#${row.build_number}` : shortDate(observationTimestamp(row)),
      passed: series === 'passed' ? rate : null,
      soft: series === 'soft' ? rate : null,
      hard: series === 'hard' ? rate : null,
      rate,
      completion: observationDurationMinutes(row),
      wait: observationWaitMinutes(row),
    }
  }),
)
const tickEvery = computed(() => Math.max(1, Math.ceil(points.value.length / 8)))
const ticks = computed(() => points.value.filter((p) => p.index % tickEvery.value === 0).map((p) => p.index))
const tickLabel = (i: number) => points.value[i]?.label ?? ''
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-5xl">
      <template v-if="state?.kind === 'aggregate'">
        <SheetHeader>
          <SheetTitle>{{ state.name }}</SheetTitle>
          <SheetDescription>Test-group detail</SheetDescription>
        </SheetHeader>
        <div class="flex flex-col gap-4 px-4 pb-6" data-testid="group-detail">
          <p class="text-sm text-muted-foreground">
            The aggregate is available, but this snapshot does not include exact per-run observations for this group.
          </p>
          <DetailFields :fields="state.fields" />
          <Button v-if="state.canLoadHistory" class="self-start" :disabled="loadState === 'loading'" @click="loadHistory">
            {{ loadState === 'loading' ? 'Loading run history…' : loadState === 'failed' ? 'Retry loading run history' : 'Load 30-day run history' }}
          </Button>
        </div>
      </template>
      <template v-else-if="state?.kind === 'evidence' && candidate">
        <SheetHeader>
          <SheetTitle>{{ candidate.name || 'Group evidence' }}</SheetTitle>
          <SheetDescription>
            {{ allObservations.length ? 'Historical outcomes, latency, and exact Buildkite evidence' : `${state.scopeLabel} evidence` }}
          </SheetDescription>
        </SheetHeader>
        <div class="flex flex-col gap-4 px-4 pb-6" data-testid="group-evidence">
          <Alert :class="notice.tone === 'info' ? 'border-info/40 bg-info/10' : 'border-warning/40 bg-warning/10'">
            <AlertDescription class="text-foreground">
              <p><strong>{{ notice.strong }}</strong>{{ notice.text }}</p>
            </AlertDescription>
          </Alert>
          <p v-if="!allObservations.length" class="text-sm text-muted-foreground">
            The aggregate is available, but this snapshot predates per-run evidence. Regenerate operations_v2.json.gz to populate exact links.
          </p>
          <template v-else>
            <div class="flex flex-wrap items-center gap-3">
              <ToggleGroup v-model="mode" type="single" variant="outline" size="sm" aria-label="Test-group history cohort">
                <ToggleGroupItem value="main">All main</ToggleGroupItem>
                <ToggleGroupItem value="nightly">Nightly only</ToggleGroupItem>
              </ToggleGroup>
              <span class="text-xs text-muted-foreground">
                {{ complete ? 'Choose the complete branch=main cohort or its nightly subset.' : 'Choose the published branch=main evidence or its nightly subset; omitted rows are not inferred.' }}
              </span>
            </div>
            <p v-if="!observations.length" class="py-6 text-center text-sm text-muted-foreground">
              No exact nightly observations are retained for this strict test-group variant.
            </p>
            <template v-else>
              <div class="grid grid-cols-2 gap-3 sm:grid-cols-5">
                <div v-for="stat in summary" :key="stat.label" class="rounded-lg border p-3">
                  <p class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ stat.label }}</p>
                  <p class="mt-1 text-xl font-semibold tabular-nums" :class="STAT_TEXT[stat.tone]">{{ stat.value }}</p>
                </div>
              </div>
              <div class="grid gap-4" :class="hasDuration ? 'lg:grid-cols-2' : ''">
                <section class="rounded-lg border p-3">
                  <h3 class="text-sm font-semibold">Outcome trend</h3>
                  <p class="mb-4 text-xs text-muted-foreground">{{ outcomeChartCaption(observations, complete) }}</p>
                  <ChartContainer :config="outcomeConfig" class="aspect-auto h-[200px] w-full">
                    <VisXYContainer :data="points" :x-domain="[-0.5, points.length - 0.5]" :y-domain="[0, 100]" :margin="{ left: 4, right: 4 }">
                      <VisStackedBar
                        :x="(d: Point) => d.index"
                        :y="[(d: Point) => d.passed ?? 0, (d: Point) => d.soft ?? 0, (d: Point) => d.hard ?? 0]"
                        :color="[outcomeConfig.passed.color, outcomeConfig.soft.color, outcomeConfig.hard.color]"
                        :rounded-corners="2"
                        :bar-max-width="18"
                      />
                      <VisLine :x="(d: Point) => d.index" :y="(d: Point) => d.rate ?? undefined" :color="outcomeConfig.rate.color" curve-type="step" />
                      <VisAxis type="x" :tick-line="false" :domain-line="false" :grid-line="false" :tick-values="ticks" :tick-format="tickLabel" />
                      <VisAxis type="y" :num-ticks="5" :tick-line="false" :domain-line="false" :tick-format="(v: number) => `${v}%`" />
                    </VisXYContainer>
                    <ChartLegendContent />
                  </ChartContainer>
                </section>
                <section v-if="hasDuration" class="rounded-lg border p-3">
                  <h3 class="text-sm font-semibold">Completion and queue wait</h3>
                  <p class="mb-4 text-xs text-muted-foreground">Minutes per exact Buildkite job observation</p>
                  <ChartContainer :config="durationConfig" class="aspect-auto h-[200px] w-full">
                    <VisXYContainer :data="points" :margin="{ left: 4, right: 4 }">
                      <VisLine
                        :x="(d: Point) => d.index"
                        :y="hasWait ? [(d: Point) => d.completion ?? undefined, (d: Point) => d.wait ?? undefined] : [(d: Point) => d.completion ?? undefined]"
                        :color="hasWait ? ['var(--chart-2)', 'var(--warning)'] : ['var(--chart-2)']"
                      />
                      <VisAxis type="x" :tick-line="false" :domain-line="false" :grid-line="false" :tick-values="ticks" :tick-format="tickLabel" />
                      <VisAxis type="y" :num-ticks="4" :tick-line="false" :domain-line="false" />
                    </VisXYContainer>
                    <ChartLegendContent />
                  </ChartContainer>
                </section>
              </div>
              <div class="flex flex-wrap items-center gap-2">
                <Input v-model="query" type="search" placeholder="Filter build, queue, or result" aria-label="Filter reliability observations" class="h-8 max-w-sm" />
                <Select v-model="resultFilter">
                  <SelectTrigger size="sm" aria-label="Filter observations by result"><SelectValue /></SelectTrigger>
                  <SelectContent>
                    <SelectItem value="all">All results</SelectItem>
                    <SelectItem value="passing">Passing only</SelectItem>
                    <SelectItem value="incident">Failures only</SelectItem>
                  </SelectContent>
                </Select>
                <span class="ml-auto text-sm text-muted-foreground">
                  {{ integer(evidenceRows.length) }} of {{ integer(observations.length) }} retained {{ mode === 'main' ? 'main' : 'nightly' }} observations
                </span>
              </div>
              <div class="overflow-x-auto rounded-lg border">
                <Table>
                  <TableHeader class="bg-muted">
                    <TableRow>
                      <TableHead>Build</TableHead>
                      <TableHead>Cohort</TableHead>
                      <TableHead>Observed</TableHead>
                      <TableHead>Result</TableHead>
                      <TableHead>Variant</TableHead>
                      <TableHead>Queue</TableHead>
                      <TableHead class="text-right">Completion</TableHead>
                      <TableHead class="text-right">Queue wait</TableHead>
                      <TableHead>Retry evidence</TableHead>
                      <TableHead>Job evidence</TableHead>
                    </TableRow>
                  </TableHeader>
                  <TableBody>
                    <TableRow v-for="(row, index) in evidenceRows" :key="index">
                      <TableCell>
                        <a :href="exactPipelineEvidenceUrl(row, sourcePipeline)" target="_blank" rel="noopener" class="font-mono hover:underline">
                          {{ row.build_number ? `#${row.build_number}` : 'Build' }}
                        </a>
                      </TableCell>
                      <TableCell>
                        <ToneBadge :tone="isNightlyObservation(row) ? 'info' : 'neutral'">{{ isNightlyObservation(row) ? 'nightly' : 'main' }}</ToneBadge>
                      </TableCell>
                      <TableCell>{{ shortDate(observationTimestamp(row)) }}</TableCell>
                      <TableCell><ToneBadge :tone="toneForState(evidenceResultLabel(row))">{{ evidenceResultLabel(row) }}</ToneBadge></TableCell>
                      <TableCell class="font-mono text-xs whitespace-normal">
                        {{ [row.variant_hardware, (row.variant_queues || []).join(', '), row.variant_id ? `id ${row.variant_id}` : null].filter(Boolean).join(' - ') || value(row.group_id) }}
                      </TableCell>
                      <TableCell class="font-mono text-xs">{{ value(row.queue) }}</TableCell>
                      <TableCell class="text-right tabular-nums">{{ duration(observationDurationMinutes(row)) }}</TableCell>
                      <TableCell class="text-right tabular-nums">{{ duration(observationWaitMinutes(row)) }}</TableCell>
                      <TableCell>
                        <ToneBadge
                          v-if="(row.retry_evidence || row).retried || (row.retry_evidence || row).retried_in_job_id || Number((row.retry_evidence || row).retries_count || 0)"
                          tone="info"
                          >{{ Number((row.retry_evidence || row).retries_count || 0) ? `${Number((row.retry_evidence || row).retries_count)} retries` : 'retried' }}</ToneBadge
                        >
                        <span v-else class="text-muted-foreground">-</span>
                      </TableCell>
                      <TableCell>
                        <a :href="exactPipelineEvidenceUrl(row, sourcePipeline)" target="_blank" rel="noopener" class="hover:underline">Open log ↗</a>
                      </TableCell>
                    </TableRow>
                    <TableRow v-if="!evidenceRows.length">
                      <TableCell colspan="10" class="h-16 text-center text-muted-foreground">No matching observations.</TableCell>
                    </TableRow>
                  </TableBody>
                </Table>
              </div>
              <p class="text-xs text-muted-foreground">
                Method: outcomes combine Buildkite job state with parsed test-result summaries. Completion is job wall time and queue wait is shown only when the collector retained it. Retry badges require explicit Buildkite retry metadata; mixed outcomes alone are not labeled as confirmed flakes.
              </p>
            </template>
          </template>
        </div>
      </template>
    </SheetContent>
  </Sheet>
</template>
