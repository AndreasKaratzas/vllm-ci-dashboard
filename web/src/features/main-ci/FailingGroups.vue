<script setup lang="ts">
import { RefreshCw } from '@lucide/vue'
import { computed, effectScope, onBeforeUnmount } from 'vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import DataTable from '@/components/table/DataTable.vue'
import { badge, hardwareCell, link, type ColumnSpec } from '@/components/table/columns'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useDataset } from '@/composables/useDataset'
import { integer } from '@/domain/format'
import { buildFailingGroups, groupLabel, type FailingGroup, type MainBuild } from '@/domain/mainCi'
import { TONE_LABEL } from '@/lib/palette'

// The failing groups of one build come from the 30-day run ledger, loaded once per page when a
// build with failures is opened; it is never refreshed in the background.
const props = defineProps<{ build: MainBuild }>()

const scope = effectScope()
onBeforeUnmount(() => scope.stop())
const ledger = scope.run(() => useDataset({ sections: ['reliability'] }))!

const reliability = computed(() => ledger.data.value?.reliability ?? null)
const available = computed(() => !!reliability.value && reliability.value.available !== false)
const result = computed(() => (available.value ? buildFailingGroups(reliability.value, props.build.number) : { rows: [], retained: 0 }))
const counted = computed(() => (props.build.amd ? props.build.amd.hard + props.build.amd.soft : 0))
const missing = computed(() => Math.max(0, counted.value - result.value.rows.length))

const columns: ColumnSpec<FailingGroup>[] = [
  { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ ...link(groupLabel(row.name), row.jobUrl), sub: row.queue || undefined }) },
  { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
  { id: 'result', label: 'Result', value: (row) => row.severity, cell: (row) => badge(TONE_LABEL[row.severity], row.severity), class: 'whitespace-nowrap' },
  {
    id: 'attempts',
    label: 'Attempts',
    numeric: true,
    value: (row) => row.attempts,
    cell: (row) => ({ text: integer(row.attempts), muted: row.attempts < 2, sub: row.attempts > 1 ? 'retried' : undefined }),
    class: 'hidden sm:table-cell',
  },
]
</script>

<template>
  <div class="flex flex-col gap-3" data-testid="main-build-failing-groups">
    <div v-if="ledger.loading.value" class="flex flex-col gap-2" aria-busy="true">
      <span class="text-xs text-muted-foreground">Loading the 30-day run ledger…</span>
      <Skeleton v-for="i in 3" :key="i" class="h-9" />
    </div>
    <EmptyState v-else-if="ledger.error.value && !ledger.data.value" title="The run ledger did not load" :description="ledger.error.value">
      <template #action>
        <Button size="sm" variant="outline" @click="ledger.refresh()"><RefreshCw /> Retry</Button>
      </template>
    </EmptyState>
    <Callout v-else-if="!available" tone="neutral" title="Run ledger unavailable">
      This snapshot does not publish the 30-day run ledger; open the build on Buildkite for its failing jobs.
    </Callout>
    <template v-else>
      <Callout v-if="missing" tone="neutral" data-testid="main-build-failing-missing">
        {{ integer(missing) }} of {{ integer(counted) }} failing groups are past the ledger's per-group history; the Buildkite build lists every job.
      </Callout>
      <DataTable
        v-if="result.rows.length"
        :columns="columns"
        :rows="result.rows"
        :page-size="10"
        :searchable="result.rows.length > 10"
        :column-menu="false"
        :search-text="(row: FailingGroup) => [row.name, row.hardware, row.queue].join(' ')"
        label="Failing AMD groups"
      />
    </template>
  </div>
</template>
