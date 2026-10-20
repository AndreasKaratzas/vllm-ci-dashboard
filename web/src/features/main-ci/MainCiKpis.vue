<script setup lang="ts">
import { computed } from 'vue'
import ProportionBar from '@/components/charts/ProportionBar.vue'
import KpiCard, { type KpiAction } from '@/components/stat/KpiCard.vue'
import StatGrid from '@/components/stat/StatGrid.vue'
import { useClock } from '@/composables/useClock'
import { age, integer, type Rec } from '@/domain/format'
import { buildOutcome, buildTone, previousDayRate, trendPoints, type MainBuild, type MainCiSummary } from '@/domain/mainCi'
import { rateTone, statusVar } from '@/lib/palette'

const props = defineProps<{
  summary: MainCiSummary
  record: Rec | null
  builds: MainBuild[]
  actions: { latest?: KpiAction; rate?: KpiAction; incidents?: KpiAction; flaky?: KpiAction }
}>()

const now = useClock()
const latest = computed(() => props.builds[0] ?? props.summary.latest)
// The latest builds' hard failures, oldest first, so a run of red reads as a rising line.
const hardSpark = computed(() => props.builds.slice(0, 30).reverse().map((build) => build.amd?.hard ?? 0))

const day = computed(() => props.summary.last24h)
const rate = computed(() => day.value?.passRate ?? null)
const previous = computed(() => previousDayRate(props.record))
const rateDelta = computed(() => {
  if (rate.value === null || previous.value === null) return undefined
  const change = Math.round((rate.value - previous.value) * 10) / 10
  return { value: change, goodWhen: 'up' as const, text: `${change > 0 ? '+' : ''}${change.toFixed(1)} pts`, label: 'vs the 24 hours before' }
})
const rateSpark = computed(() => trendPoints(props.record, '7d').flatMap((point) => (point.passRate === null ? [] : [point.passRate])))
const rateStatus = computed(() => {
  const tone = rateTone(rate.value)
  return tone === 'hard' ? { tone, label: 'Below 95%' } : tone === 'soft' ? { tone, label: 'Below 99%' } : undefined
})

const active = computed(() => props.summary.activeIncidents ?? 0)
const hard = computed(() => props.summary.hardIncidents ?? 0)
const pending = computed(() => props.summary.pendingSoft ?? 0)
const incidentSegments = computed(() => [
  { key: 'hard', label: 'Hard', count: hard.value, color: statusVar('hard') },
  { key: 'soft', label: 'Soft', count: active.value - hard.value, color: statusVar('soft') },
])
const flaky = computed(() => props.summary.alertingFlaky ?? 0)
const retry = computed(() => props.summary.alertingRetry ?? 0)
const percent = (value: number) => `${value.toFixed(1)}%`
</script>

<template>
  <StatGrid data-testid="main-kpis">
    <KpiCard
      label="Latest main build"
      :value="latest ? `#${latest.number}` : null"
      :status="latest ? { tone: buildTone(latest), label: buildOutcome(latest) } : undefined"
      :spark="hardSpark.length > 1 && hardSpark.some(Boolean) ? hardSpark : undefined"
      :headline="actions.latest?.label"
      :footnote="
        latest
          ? `${latest.amd ? `${integer(latest.amd.passed)} AMD jobs passed${latest.amd.retried ? ` · ${integer(latest.amd.retried)} retried` : ''} · ` : ''}${age(latest.createdAt, now)}`
          : 'No completed main build in the cohort'
      "
      :action="latest ? actions.latest : undefined"
      data-testid="kpi-main-latest"
    />
    <KpiCard
      label="AMD pass rate, 24h"
      :value="rate"
      badges="inline"
      :format="percent"
      :empty="rate === null ? 'No AMD verdict in 24h' : undefined"
      :status="rateStatus"
      :delta="rateDelta"
      :spark="rateSpark"
      :headline="actions.rate?.label"
      :footnote="day ? `${integer(day.greenBuilds)} of ${integer(day.amdBuilds)} builds without a hard failure · ${integer(day.hard)} hard jobs` : undefined"
      :action="actions.rate"
      data-testid="kpi-main-rate"
    />
    <KpiCard
      label="Active main incidents"
      :value="summary.activeIncidents"
      :unit="active === 1 ? 'group' : 'groups'"
      :status="active ? { tone: hard ? 'hard' : 'soft', label: hard ? `${integer(hard)} hard` : 'Soft' } : undefined"
      :headline="actions.incidents?.label"
      :footnote="active ? `${integer(hard)} hard · ${integer(active - hard)} soft · ${integer(pending)} pending soft` : pending ? `${integer(pending)} pending soft` : 'No confirmed failure on main'"
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
      :footnote="`${summary.flakyGroups === null ? '' : `${integer(summary.flakyGroups)} flipped in 30 days · `}${integer(retry)} retry ${retry === 1 ? 'alert' : 'alerts'}`"
      :action="actions.flaky"
      data-testid="kpi-main-flaky"
    />
  </StatGrid>
</template>
