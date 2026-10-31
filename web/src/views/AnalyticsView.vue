<script setup lang="ts">
import { computed, type Component } from 'vue'
import PageHeader, { type Freshness } from '@/components/layout/PageHeader.vue'
import { SUB_VIEWS, subViewIds } from '@/components/layout/navigation'
import SubNav from '@/components/layout/SubNav.vue'
import { useQueryState } from '@/composables/useQueryState'
import { provideSubviewHeader } from '@/composables/useSubviewHeader'
import { shortDate } from '@/domain/format'
import { useFreshness } from '@/features/freshness/useFreshness'
import AnalyticsAgentHealth from './analytics/AnalyticsAgentHealth.vue'
import AnalyticsDns from './analytics/AnalyticsDns.vue'
import AnalyticsDocker from './analytics/AnalyticsDocker.vue'
import AnalyticsGroups from './analytics/AnalyticsGroups.vue'
import AnalyticsLatency from './analytics/AnalyticsLatency.vue'
import AnalyticsNightlies from './analytics/AnalyticsNightlies.vue'
import AnalyticsSlowdowns from './analytics/AnalyticsSlowdowns.vue'

interface AnalyticsSubview {
  description: string
  component: Component
  /** The manifest source whose collection age the header shows, unless the sub-view publishes its own. */
  source: string
}

// Each sub-view owns its data loading and its file under views/analytics/; ids and labels live in SUB_VIEWS.
const INFO: Record<string, AnalyticsSubview> = {
  groups: {
    description: 'AMD test-group health on main CI across nightlies, hardware, and exact job variants.',
    component: AnalyticsGroups,
    source: 'analytics',
  },
  latency: {
    description: 'Recent main CI AMD MI test-group timing against the upstream baseline.',
    component: AnalyticsLatency,
    source: 'analytics',
  },
  slowdowns: {
    description: 'AMD MI test steps on main that became sustainably slower, and the change that slowed them.',
    component: AnalyticsSlowdowns,
    source: 'main_ci',
  },
  nightlies: {
    description: 'Main CI AMD MI nightly outcomes and their failing test groups.',
    component: AnalyticsNightlies,
    source: 'analytics',
  },
  dns: {
    description: 'DNS resolver failures seen in AMD CI job logs, by queue and physical node, with exact Buildkite evidence.',
    component: AnalyticsDns,
    source: 'dns',
  },
  'agent-health': {
    description: 'Failures attributed to physical AMD CI nodes, their clustering in time, and the runs behind them.',
    component: AnalyticsAgentHealth,
    source: 'agent_health',
  },
  docker: {
    description: 'How often the ROCm base image cache hits, what a miss costs, and how long the test image takes, for main and PR builds.',
    component: AnalyticsDocker,
    source: 'docker_builds',
  },
}
const VIEWS = SUB_VIEWS.analytics.items.map((item) => ({ ...item, ...INFO[item.value]! }))

const view = useQueryState('view', { default: SUB_VIEWS.analytics.default, allowed: subViewIds('analytics') })
const current = computed(() => VIEWS.find((v) => v.value === view.value) ?? VIEWS[0]!)
const subview = provideSubviewHeader()

const sourceChip = useFreshness().chip(() => [current.value.source])
const freshness = computed<Freshness | undefined>(() => {
  if (subview.value?.freshness) return subview.value.freshness
  const chip = sourceChip.value
  const observedAt = subview.value?.observedAt
  return chip && observedAt ? { ...chip, detail: `Observed ${shortDate(observedAt)} · ${chip.detail}` } : chip
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader title="CI Analytics" :description="current.description" :freshness="freshness">
      <template v-if="subview?.about" #about>
        <component :is="subview.about" />
      </template>
    </PageHeader>
    <SubNav :items="SUB_VIEWS.analytics.items" :default-value="SUB_VIEWS.analytics.default" label="CI Analytics sections" />
    <component :is="current.component" :key="current.value" />
  </div>
</template>
