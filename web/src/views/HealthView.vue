<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed, watch } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import Callout from '@/components/feedback/Callout.vue'
import PageHeader, { type Freshness } from '@/components/layout/PageHeader.vue'
import { SUB_VIEWS, subViewIds } from '@/components/layout/navigation'
import SubNav from '@/components/layout/SubNav.vue'
import { Button } from '@/components/ui/button'
import { useOperationSections } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import { shortDate, type Rec } from '@/domain/format'
import { ciHealthPublicationRetentionMessage, latestAmd } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'
import { useFreshness } from '@/features/freshness/useFreshness'
import HealthAbout from './health/HealthAbout.vue'
import HealthHardware from './health/HealthHardware.vue'
import HealthMirrors from './health/HealthMirrors.vue'
import HealthOverview from './health/HealthOverview.vue'
import HealthParity from './health/HealthParity.vue'
import { HEALTH_VIEWS, type HealthViewId } from './health/views'

const view = useQueryState<HealthViewId>('view', { default: SUB_VIEWS.health.default as HealthViewId, allowed: subViewIds<HealthViewId>('health') })
const current = computed(() => HEALTH_VIEWS.find((v) => v.value === view.value)!)

const { data: ops, error, loading, retry } = useOperationSections(['nightly', 'amd_test_health', 'test_group_parity'])
const sheets = useSheetStack()
watch(view, () => sheets.close())

const amd = computed(() => latestAmd(ops.value))
const build = computed<Rec>(() => (amd.value.builds || [])[0] || {})
const retentionMessage = computed(() => ciHealthPublicationRetentionMessage(amd.value))

const SOURCE_COMMIT = /^[0-9a-f]{40}$/
const header = computed<{ observedAt: unknown; action: { label: string; url: string } | null }>(() => {
  const o = ops.value ?? {}
  const parity: Rec = o.test_group_parity || {}
  if (view.value === 'overview') {
    const url = exactPipelineBuildUrl(build.value, 'ci')
    return { observedAt: url ? build.value.created_at || o.generated_at : o.generated_at, action: url ? { label: `Open main CI AMD nightly #${build.value.number}`, url } : null }
  }
  if (view.value === 'parity') {
    const source: Rec = parity.source || {}
    const commit = source.main_commit || source.commit_sha || ''
    const url = source.main_commit_url || (commit ? `https://github.com/vllm-project/vllm/commit/${commit}` : '')
    return { observedAt: parity.generated_at || o.generated_at, action: url ? { label: 'Open current main CI source', url } : null }
  }
  if (view.value === 'mirrors') {
    const source: Rec = (parity.mirror_inventory || {}).source || {}
    const commit = source.current_definition_commit_sha || source.main_commit || ''
    return {
      observedAt: parity.generated_at || o.generated_at,
      action: SOURCE_COMMIT.test(commit) ? { label: 'Open current main CI source', url: `https://github.com/vllm-project/vllm/commit/${commit}` } : null,
    }
  }
  return { observedAt: o.generated_at, action: null }
})

const sourcesChip = useFreshness().chip(() => current.value.sources)
const freshness = computed<Freshness | undefined>(() => {
  const chip = sourcesChip.value
  if (!chip || !header.value.observedAt) return chip
  return { ...chip, detail: `Observed ${shortDate(header.value.observedAt)} · ${chip.detail}` }
})
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader title="CI Health" :description="current.description" :freshness="freshness">
      <template #about>
        <HealthAbout :view="view" :ops="ops" />
      </template>
      <template v-if="header.action" #actions>
        <Button variant="outline" size="sm" as="a" :href="header.action.url" target="_blank" rel="noopener">
          {{ header.action.label }} <ExternalLink />
        </Button>
      </template>
    </PageHeader>
    <SubNav :items="SUB_VIEWS.health.items" :default-value="SUB_VIEWS.health.default" label="CI Health sections" />
    <Callout v-if="retentionMessage" tone="soft" title="Partial build history">{{ retentionMessage }}</Callout>

    <AsyncBoundary :loading="loading && !ops" :error="error" @retry="retry">
      <template v-if="ops">
        <HealthOverview v-if="view === 'overview'" :ops="ops" :sheets="sheets" />
        <HealthParity v-else-if="view === 'parity'" :ops="ops" :sheets="sheets" />
        <HealthHardware v-else-if="view === 'coverage'" :ops="ops" :sheets="sheets" />
        <HealthMirrors v-else :ops="ops" :sheets="sheets" />
      </template>
    </AsyncBoundary>
    <SheetStack :sheets="sheets" />
  </div>
</template>
