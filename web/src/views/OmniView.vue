<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { computed, ref } from 'vue'
import { useRoute, useRouter } from 'vue-router'
import OmniDetailSheet from '@/components/omni/OmniDetailSheet.vue'
import ViewState from '@/components/ops/ViewState.vue'
import { Button } from '@/components/ui/button'
import { useOperationSections } from '@/composables/useOperations'
import { age, shortDate } from '@/domain/format'
import { omniRouteValue, omniView, type OmniDetail } from '@/domain/omni'
import OmniLive from './omni/OmniLive.vue'
import OmniMapping from './omni/OmniMapping.vue'

const route = useRoute()
const router = useRouter()

// Classic operationSectionNames('ci-omni'); loadOperations arbitrates the live queue section.
const { data: ops, error, loading, retry } = useOperationSections(['omni', 'queue'])

const mappingRange = computed(() => omniRouteValue('omni_mapping_range', route.query.mapping))
const omniRange = computed(() => omniRouteValue('omni_range', route.query.range))
function setQuery(key: 'mapping' | 'range', id: string, fallback: string) {
  void router.replace({ query: { ...route.query, [key]: id === fallback ? undefined : id } })
}

const page = computed(() => (ops.value ? omniView(ops.value, mappingRange.value, omniRange.value) : null))
const view = computed(() => (page.value && page.value.available ? page.value : null))

// Classic stacks drawers with a back button; each drill-down pushes a sheet.
const stack = ref<OmniDetail[]>([])
function openDetail(detail: OmniDetail) {
  stack.value = [detail]
}
function drill(detail: OmniDetail) {
  stack.value = [...stack.value, detail]
}
function closeFrom(index: number, open: boolean) {
  if (!open) stack.value = stack.value.slice(0, index)
}
</script>

<template>
  <div class="flex flex-col gap-4 md:gap-6">
    <div class="flex flex-wrap items-end justify-between gap-3">
      <div>
        <h2 class="text-2xl font-semibold tracking-tight">Omni CI</h2>
        <p class="max-w-3xl text-sm text-muted-foreground" data-testid="omni-description">
          {{ view?.header.description ?? 'Incoming vllm-project/vllm-omni workload and its impact on the AMD queues shared with vllm-project/vllm.' }}
        </p>
      </div>
      <div v-if="view" class="flex items-center gap-3">
        <span v-if="view.header.observedAt" class="text-xs text-muted-foreground" :title="view.header.observedAt" data-testid="omni-observed">
          Observed {{ shortDate(view.header.observedAt) }} · {{ age(view.header.observedAt) }}
        </span>
        <Button variant="outline" size="sm" as="a" :href="view.header.actionUrl" target="_blank" rel="noopener">
          {{ view.header.actionLabel }} <ExternalLink />
        </Button>
      </div>
    </div>

    <ViewState :loading="loading && !ops" :error="error" @retry="retry">
      <p
        v-if="page && !page.available"
        class="rounded-md border border-warning/40 bg-warning/10 p-3 text-sm text-warning"
        data-testid="omni-unavailable"
      >
        {{ page.message }}
      </p>
      <template v-else-if="view">
        <OmniMapping :view="view" @open="openDetail" @range="(id) => setQuery('mapping', id, '7d')" />
        <OmniLive :view="view" @open="openDetail" @range="(id) => setQuery('range', id, '24h')" />
      </template>
    </ViewState>

    <OmniDetailSheet
      v-for="(detail, index) in stack"
      :key="`${index}:${detail.id}`"
      :open="true"
      :detail="detail"
      :depth="index"
      @update:open="(open) => closeFrom(index, open)"
      @drill="drill"
    />
  </div>
</template>
