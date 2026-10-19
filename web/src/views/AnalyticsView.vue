<script setup lang="ts">
import { computed, type Component } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import AnalyticsAgentHealth from './analytics/AnalyticsAgentHealth.vue'
import AnalyticsDns from './analytics/AnalyticsDns.vue'
import AnalyticsGroups from './analytics/AnalyticsGroups.vue'
import AnalyticsLatency from './analytics/AnalyticsLatency.vue'
import AnalyticsNightlies from './analytics/AnalyticsNightlies.vue'

// Each sub-view owns its data loading and its file under views/analytics/.
const VIEWS: { id: string; label: string; component: Component }[] = [
  { id: 'groups', label: 'AMD test health', component: AnalyticsGroups },
  { id: 'latency', label: 'AMD nightly latency', component: AnalyticsLatency },
  { id: 'nightlies', label: 'AMD nightlies', component: AnalyticsNightlies },
  { id: 'dns', label: 'DNS health', component: AnalyticsDns },
  { id: 'agent-health', label: 'CI agent health', component: AnalyticsAgentHealth },
]

const route = useRoute()
const router = useRouter()
const view = computed<string>({
  get: () => (VIEWS.some((v) => v.id === route.query.view) ? String(route.query.view) : 'groups'),
  set: (id) => void router.replace({ query: { ...route.query, view: id === 'groups' ? undefined : id } }),
})
const current = computed(() => VIEWS.find((v) => v.id === view.value)!)
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <h2 class="text-2xl font-semibold tracking-tight">CI Analytics</h2>
    <Tabs v-model="view" class="max-w-full">
      <!-- Scroll instead of overflowing the page on narrow screens. -->
      <TabsList class="max-w-full justify-start overflow-x-auto">
        <TabsTrigger v-for="v in VIEWS" :key="v.id" :value="v.id">{{ v.label }}</TabsTrigger>
      </TabsList>
    </Tabs>
    <component :is="current.component" />
  </div>
</template>
