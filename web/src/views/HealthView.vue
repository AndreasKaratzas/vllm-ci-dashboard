<script setup lang="ts">
import { computed } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { useOperationSections } from '@/composables/useOperations'
import { age, shortDate, type Rec } from '@/domain/format'
import { ciHealthPublicationRetentionMessage, latestAmd } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'
import HealthHardware from './health/HealthHardware.vue'
import HealthMirrors from './health/HealthMirrors.vue'
import HealthOverview from './health/HealthOverview.vue'
import HealthParity from './health/HealthParity.vue'

const VIEWS = [
  { id: 'overview', label: 'Overview' },
  { id: 'parity', label: 'Upstream parity' },
  { id: 'coverage', label: 'AMD hardware' },
  { id: 'mirrors', label: 'AMD mirrors' },
] as const
type ViewId = (typeof VIEWS)[number]['id']

const DESCRIPTIONS: Record<ViewId, string> = {
  overview: 'Latest main CI AMD gating-job outcomes and failure movement. Logical test groups are separate from exact Buildkite job variants.',
  parity: 'Current main CI AMD route coverage against upstream source definitions. Configured route modes and runtime pass/fail are separate.',
  coverage: 'Configured AMD test groups by architecture and the fixed best-hardware health policy.',
  mirrors: 'Current AMD mirror declarations parsed from every .buildkite/test_areas/*.yaml file on vLLM main.',
}

const route = useRoute()
const router = useRouter()
const view = computed<ViewId>({
  get: () => (VIEWS.some((v) => v.id === route.query.view) ? (route.query.view as ViewId) : 'overview'),
  set: (id) => void router.replace({ query: { ...route.query, view: id === 'overview' ? undefined : id } }),
})

const { data: ops, error, loading, retry } = useOperationSections(['nightly', 'amd_test_health', 'test_group_parity'])

const amd = computed(() => latestAmd(ops.value))
const build = computed<Rec>(() => (amd.value.builds || [])[0] || {})
const retentionMessage = computed(() => ciHealthPublicationRetentionMessage(amd.value))
const header = computed(() => {
  const o = ops.value ?? {}
  if (view.value === 'overview' && exactPipelineBuildUrl(build.value, 'ci')) {
    return {
      observedAt: build.value.created_at || o.generated_at,
      action: { label: `Open main CI AMD nightly #${build.value.number} ↗`, url: exactPipelineBuildUrl(build.value, 'ci') },
    }
  }
  if (view.value === 'parity') {
    const source = (o.test_group_parity || {}).source || {}
    const commit = source.main_commit || source.commit_sha || ''
    const url = source.main_commit_url || (commit ? `https://github.com/vllm-project/vllm/commit/${commit}` : '')
    return { observedAt: (o.test_group_parity || {}).generated_at || o.generated_at, action: url ? { label: 'Open current main CI source ↗', url } : null }
  }
  return { observedAt: o.generated_at, action: null }
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight">CI Health</h2>
        <p class="max-w-3xl text-sm text-muted-foreground">{{ DESCRIPTIONS[view] }}</p>
      </div>
      <div class="flex items-center gap-3">
        <span v-if="header.observedAt" class="text-xs text-muted-foreground" :title="header.observedAt">
          Observed {{ shortDate(header.observedAt) }} · {{ age(header.observedAt) }}
        </span>
        <Button v-if="header.action" variant="outline" size="sm" as="a" :href="header.action.url" target="_blank" rel="noopener">
          {{ header.action.label }}
        </Button>
      </div>
    </div>
    <Tabs v-model="view">
      <TabsList>
        <TabsTrigger v-for="v in VIEWS" :key="v.id" :value="v.id">{{ v.label }}</TabsTrigger>
      </TabsList>
    </Tabs>
    <p v-if="retentionMessage" class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning">{{ retentionMessage }}</p>

    <HealthHardware v-if="view === 'coverage'" />
    <HealthMirrors v-else-if="view === 'mirrors'" />
    <ViewState v-else :loading="loading && !ops" :error="error" @retry="retry">
      <HealthOverview v-if="view === 'overview' && ops" :ops="ops" />
      <HealthParity v-else-if="view === 'parity' && ops" :ops="ops" />
    </ViewState>
  </div>
</template>
