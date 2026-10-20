<script setup lang="ts">
import { ExternalLink, ServerOff, SlidersHorizontal } from '@lucide/vue'
import { computed } from 'vue'
import SheetStack from '@/components/evidence/SheetStack.vue'
import type { Detail } from '@/components/evidence/detail'
import AsyncBoundary from '@/components/feedback/AsyncBoundary.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import PageHeader from '@/components/layout/PageHeader.vue'
import { SUB_VIEWS, subViewIds } from '@/components/layout/navigation'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import SubNav from '@/components/layout/SubNav.vue'
import { Button } from '@/components/ui/button'
import { useClock } from '@/composables/useClock'
import { useDataset } from '@/composables/useDataset'
import { useQueryState } from '@/composables/useQueryState'
import { useSheetStack } from '@/composables/useSheetStack'
import type { Rec } from '@/domain/format'
import { areaFreshness, sourceFreshness } from '@/domain/freshness'
import { OMNI_MAPPING_WINDOWS, OMNI_RANGE_WINDOWS, OMNI_REPOSITORIES, OMNI_ROUTE_DEFAULTS, omniView } from '@/domain/omni'
import { freshnessChip } from '@/features/freshness/freshness'
import CoverageChip from './omni/CoverageChip.vue'
import OmniLive from './omni/OmniLive.vue'
import OmniWorkload from './omni/OmniWorkload.vue'
import { toDetail } from './omni/present'

const MAPPING_OPTIONS = OMNI_MAPPING_WINDOWS.map((w) => ({ value: w.id, label: w.label }))
const RANGE_OPTIONS = OMNI_RANGE_WINDOWS.map((w) => ({ value: w.id, label: w.label }))
const DESCRIPTION = `Incoming ${OMNI_REPOSITORIES.omni} workload and its impact on the AMD queues shared with ${OMNI_REPOSITORIES.main}.`

// The queue section races its live branch against the published copy; the newer one wins.
const dataset = useDataset<Rec>({ sections: ['omni', 'queue'] })
const ops = dataset.data
const error = computed(() => (ops.value === null ? dataset.error.value : null))

const view = useQueryState('view', { default: SUB_VIEWS.omni.default, allowed: subViewIds('omni') })
const mapping = useQueryState('mapping', { default: OMNI_ROUTE_DEFAULTS.omni_mapping_range, allowed: MAPPING_OPTIONS.map((o) => o.value) })
const range = useQueryState('range', { default: OMNI_ROUTE_DEFAULTS.omni_range, allowed: RANGE_OPTIONS.map((o) => o.value) })

const page = computed(() => (ops.value ? omniView(ops.value, mapping.value, range.value) : null))
const model = computed(() => (page.value?.available ? page.value : null))
const now = useClock()
const freshness = computed(() => (ops.value ? freshnessChip(areaFreshness(sourceFreshness(ops.value, null, now.value), 'omni'), now.value) : undefined))

const sheets = useSheetStack()
const show = (detail: Detail) => sheets.open(detail)
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <PageHeader title="Omni CI" :description="DESCRIPTION" :freshness="freshness">
      <template #about>
        <p>
          A mapped job is a unique Buildkite command job from {{ OMNI_REPOSITORIES.omni }} or {{ OMNI_REPOSITORIES.main }} that ran on a
          monitored AMD MI queue, deduplicated by UUID inside the parent-build source window.
        </p>
        <p>Live queues is a separate snapshot of the jobs on those queues now and their recent occupancy; it is not unique-job volume.</p>
        <div v-if="model" class="flex flex-wrap gap-2 pt-1">
          <Button variant="outline" size="sm" class="text-foreground" @click="show(toDetail(model.methodology()))"><SlidersHorizontal /> Scope &amp; coverage</Button>
          <Button variant="ghost" size="sm" class="text-foreground" as="a" :href="model.header.actionUrl" target="_blank" rel="noopener">
            {{ model.header.actionLabel }} <ExternalLink />
          </Button>
        </div>
      </template>
      <template #actions>
        <CoverageChip v-if="view === 'workload' && model?.mappingView.available" :view="model" @open="(detail) => show(toDetail(detail))" />
        <SegmentedSelect v-if="view === 'workload'" v-model="mapping" :items="MAPPING_OPTIONS" label="Mapping window" :show-label="false" class="w-32 sm:w-auto" data-testid="omni-mapping-range" />
        <SegmentedSelect v-else v-model="range" :items="RANGE_OPTIONS" label="Live queue window" :show-label="false" class="w-32 sm:w-auto" data-testid="omni-range" />
      </template>
    </PageHeader>

    <SubNav :items="SUB_VIEWS.omni.items" :default-value="SUB_VIEWS.omni.default" label="Omni CI views" />

    <AsyncBoundary :loading="dataset.loading.value && !ops" :error="error" :refreshing="dataset.refreshing.value" @retry="dataset.refresh">
      <EmptyState
        v-if="page && !page.available"
        :icon="ServerOff"
        title="Omni queue data is unavailable"
        :description="page.message"
        cause="omni.hardware_scope ≠ amd_mi_gpu"
        data-testid="omni-unavailable"
      />
      <OmniWorkload v-else-if="model && view === 'workload'" :view="model" @show="show" />
      <OmniLive v-else-if="model" :view="model" @show="show" />
    </AsyncBoundary>

    <SheetStack :sheets="sheets" />
  </div>
</template>
