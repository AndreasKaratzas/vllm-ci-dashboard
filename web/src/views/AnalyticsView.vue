<script setup lang="ts">
import { computed, type Component } from 'vue'
import PageHeader, { type Freshness } from '@/components/layout/PageHeader.vue'
import SubNav from '@/components/layout/SubNav.vue'
import { useClock } from '@/composables/useClock'
import { useOperations } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { provideSubviewHeader } from '@/composables/useSubviewHeader'
import { sourceChip, sourceFreshness } from '@/domain/freshness'
import AnalyticsAgentHealth from './analytics/AnalyticsAgentHealth.vue'
import AnalyticsDns from './analytics/AnalyticsDns.vue'
import AnalyticsGroups from './analytics/AnalyticsGroups.vue'
import AnalyticsLatency from './analytics/AnalyticsLatency.vue'
import AnalyticsNightlies from './analytics/AnalyticsNightlies.vue'

interface AnalyticsSubview {
  value: string
  label: string
  description: string
  component: Component
  /** The manifest source whose collection age the header shows, unless the sub-view publishes its own. */
  source: string
}

// Each sub-view owns its data loading and its file under views/analytics/.
const VIEWS: AnalyticsSubview[] = [
  {
    value: 'groups',
    label: 'AMD test health',
    description: 'AMD test-group health on main CI across nightlies, hardware, and exact job variants.',
    component: AnalyticsGroups,
    source: 'analytics',
  },
  {
    value: 'latency',
    label: 'AMD nightly latency',
    description: 'Recent main CI AMD MI test-group timing against the upstream baseline.',
    component: AnalyticsLatency,
    source: 'analytics',
  },
  {
    value: 'nightlies',
    label: 'AMD nightlies',
    description: 'Main CI AMD MI nightly outcomes and their failing test groups.',
    component: AnalyticsNightlies,
    source: 'analytics',
  },
  {
    value: 'dns',
    label: 'DNS health',
    description: 'DNS resolver failures seen in AMD CI job logs, by queue and physical node, with exact Buildkite evidence.',
    component: AnalyticsDns,
    source: 'dns',
  },
  {
    value: 'agent-health',
    label: 'CI agent health',
    description: 'Failures attributed to physical AMD CI nodes, their clustering in time, and the runs behind them.',
    component: AnalyticsAgentHealth,
    source: 'agent_health',
  },
]

const view = useQueryState('view', { default: 'groups', allowed: VIEWS.map((v) => v.value) })
const current = computed(() => VIEWS.find((v) => v.value === view.value) ?? VIEWS[0]!)
const subview = provideSubviewHeader()

const { ops } = useOperations()
const now = useClock()
const freshness = computed<Freshness | undefined>(() => {
  if (subview.value?.freshness) return subview.value.freshness
  const row = sourceFreshness(ops.value, null, now.value).find((source) => source.key === current.value.source)
  return row && ops.value ? sourceChip(row, now.value) : undefined
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader title="CI Analytics" :description="current.description" :freshness="freshness">
      <template v-if="subview?.about" #about>
        <component :is="subview.about" />
      </template>
    </PageHeader>
    <SubNav :items="VIEWS" default-value="groups" label="CI Analytics sections" />
    <component :is="current.component" :key="current.value" />
  </div>
</template>
