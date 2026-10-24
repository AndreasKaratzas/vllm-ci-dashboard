<script setup lang="ts">
import { computed } from 'vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import KpiCard, { type KpiAction } from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { useClock } from '@/composables/useClock'
import { age, counted, integer, rateText, type Rec } from '@/domain/format'
import { buildOutcome, buildTone, concentrationText, dayRateChange, dayRateStatus, recentOutcomes, trendPoints, type MainBuild, type MainCiSummary } from '@/domain/mainCi'
import { SERIES, statusVar } from '@/lib/palette'

const props = defineProps<{
  summary: MainCiSummary
  record: Rec | null
  builds: MainBuild[]
  actions: { latest?: KpiAction; rate?: KpiAction; incidents?: KpiAction; flaky?: KpiAction }
}>()

const now = useClock()
const latest = computed(() => props.builds[0] ?? props.summary.latest)

// How the newest builds ended, so one passing build reads against the run before it.
const RECENT = 30
const recent = computed(() => recentOutcomes(props.builds, RECENT))
const recentSegments = computed(() =>
  (['pass', 'soft', 'hard', 'neutral'] as const).map((tone) => ({
    key: tone,
    label: { pass: 'passed', soft: 'soft failures', hard: 'hard failures', neutral: 'no AMD result' }[tone],
    count: recent.value[tone],
    color: statusVar(tone),
  })),
)

const day = computed(() => props.summary.last24h)
const rate = computed(() => day.value?.passRate ?? null)
const rateBadge = computed(() => dayRateStatus(day.value))
const rateFootnote = computed(() => {
  if (!day.value) return undefined
  const base = `${integer(day.value.greenBuilds)} of ${counted(day.value.amdBuilds, 'build')} without a hard failure · ${counted(day.value.hard, 'hard job')}`
  return day.value.concentration ? `${base}, ${concentrationText(day.value.concentration)}` : base
})
const change = computed(() => dayRateChange(props.summary))
const rateDelta = computed(() =>
  change.value === null
    ? undefined
    : { value: change.value, goodWhen: 'up' as const, text: `${change.value > 0 ? '+' : ''}${change.value.toFixed(1)} pts`, label: 'vs the 24 hours before' },
)
const rateSpark = computed(() => trendPoints(props.record, '7d').flatMap((point) => (point.passRate === null ? [] : [point.passRate])))

const active = computed(() => props.summary.activeIncidents ?? 0)
const hard = computed(() => props.summary.hardIncidents ?? 0)
const pending = computed(() => props.summary.pendingSoft ?? 0)
const incidentSegments = computed(() => [
  { key: 'hard', label: 'Hard', count: hard.value, color: statusVar('hard') },
  { key: 'soft', label: 'Soft', count: active.value - hard.value, color: statusVar('soft') },
])

const flaky = computed(() => props.summary.alertingFlaky ?? 0)
const flipped = computed(() => props.summary.flakyGroups ?? 0)
const retry = computed(() => props.summary.alertingRetry ?? 0)
// Alerting groups among every group that flipped in 30 days: a count, so a series color.
const flakySegments = computed(() => [{ key: 'alerting', label: 'Alerting on flips', count: flaky.value, color: SERIES.primary }])
</script>

<template>
  <StatGrid data-testid="main-kpis">
    <KpiCard
      label="Latest main build"
      :value="latest ? `#${latest.number}` : null"
      :status="latest ? { tone: buildTone(latest), label: buildOutcome(latest) } : undefined"
      :headline="actions.latest?.label"
      :footnote="
        latest
          ? `${latest.amd ? `${integer(latest.amd.passed)} AMD jobs passed${latest.amd.retried ? ` · ${integer(latest.amd.retried)} retried` : ''} · ` : ''}${age(latest.createdAt, now)}`
          : 'No completed main build in the cohort'
      "
      :action="latest ? actions.latest : undefined"
      data-testid="kpi-main-latest"
    >
      <template v-if="recent.builds > 1" #visual>
        <ProportionBar :segments="recentSegments" :label="`The last ${recent.builds} main builds`" :legend="false" />
        <p class="mt-1.5 text-xs text-muted-foreground tabular-nums" data-slot="kpi-main-recent">
          Last {{ recent.builds }} builds: {{ integer(recent.pass) }} passed<template v-if="recent.hard"> · {{ integer(recent.hard) }} hard</template><template v-if="recent.soft"> · {{ integer(recent.soft) }} soft</template>
        </p>
      </template>
    </KpiCard>
    <KpiCard
      label="AMD pass rate, 24h"
      :value="rate"
      badges="inline"
      :format="rateText"
      :empty="rate === null ? 'No AMD verdict in 24h' : undefined"
      :status="rateBadge"
      :delta="rateDelta"
      :spark="rateSpark"
      :headline="actions.rate?.label"
      :footnote="rateFootnote"
      :action="actions.rate"
      data-testid="kpi-main-rate"
    />
    <KpiCard
      label="Active main incidents"
      :value="summary.activeIncidents"
      :unit="active === 1 ? 'group' : 'groups'"
      :status="active ? { tone: hard ? 'hard' : 'soft', label: hard ? `${integer(hard)} hard` : 'Soft' } : undefined"
      :headline="actions.incidents?.label"
      :footnote="
        active
          ? `${integer(hard)} hard · ${integer(active - hard)} soft · ${integer(pending)} pending soft · each stays open until its group passes again`
          : pending
            ? `${integer(pending)} pending soft`
            : 'No confirmed failure on main'
      "
      :action="actions.incidents"
      data-testid="kpi-main-incidents"
    >
      <template v-if="active" #visual>
        <ProportionBar :segments="incidentSegments" label="Active main incidents by severity" :legend="false" />
      </template>
    </KpiCard>
    <KpiCard
      label="Flaky groups"
      :value="summary.alertingFlaky"
      unit="alerting"
      :status="flaky ? { tone: 'soft', label: 'Alerting' } : undefined"
      :headline="actions.flaky?.label"
      :footnote="`Separately, ${counted(retry, 'group')} ${retry === 1 ? 'alerts' : 'alert'} on passes after a retry`"
      :action="actions.flaky"
      data-testid="kpi-main-flaky"
    >
      <template v-if="flipped" #visual>
        <ProportionBar :segments="flakySegments" :max="flipped" :label="`Groups alerting on flips of the ${integer(flipped)} that flipped in 30 days`" :legend="false" />
        <p class="mt-1.5 text-xs text-muted-foreground tabular-nums">{{ integer(flaky) }} of {{ integer(flipped) }} groups that flipped in 30 days</p>
      </template>
    </KpiCard>
  </StatGrid>
</template>
