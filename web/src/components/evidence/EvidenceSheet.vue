<script setup lang="ts">
import { ArrowLeft, ExternalLink } from '@lucide/vue'
import { computed } from 'vue'
import Callout from '@/components/feedback/Callout.vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import Sparkline from '@/components/stat/Sparkline.vue'
import DataTable from '@/components/table/DataTable.vue'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import type { Detail } from './detail'

const props = withDefaults(defineProps<{ detail: Detail; depth?: number }>(), { depth: 0 })
const open = defineModel<boolean>('open', { required: true })
const emit = defineEmits<{ drill: [Detail]; closed: [] }>()

const WIDTH = { md: 'sm:max-w-xl', lg: 'sm:max-w-3xl', xl: 'sm:max-w-5xl' } as const
const width = computed(() => WIDTH[props.detail.width ?? 'lg'])
const fields = computed(() => (props.detail.fields ?? []).filter((f) => f.value !== null && f.value !== undefined && f.value !== ''))
function drillRow(rowDetail: (row: unknown) => Detail | undefined, row: unknown) {
  const next = rowDetail(row)
  if (next) emit('drill', next)
}
const sources = computed(() => (props.detail.sources ?? []).filter((s) => s.url))
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent :class="['w-full overflow-y-auto', width]" data-slot="evidence-sheet" :data-detail="detail.id" @close-auto-focus="emit('closed')">
      <SheetHeader class="gap-2 pr-10">
        <Button v-if="depth > 0" variant="ghost" size="sm" class="-ml-2 w-fit text-muted-foreground" @click="open = false">
          <ArrowLeft /> Back
        </Button>
        <SheetTitle class="text-lg">{{ detail.title || 'Evidence' }}</SheetTitle>
        <SheetDescription>{{ detail.subtitle || 'Source evidence and retained history' }}</SheetDescription>
        <div v-if="sources.length" class="flex flex-wrap gap-2 pt-1">
          <Button v-for="source in sources" :key="source.url + source.label" variant="outline" size="sm" as="a" :href="source.url" target="_blank" rel="noopener">
            {{ source.label }} <ExternalLink />
          </Button>
        </div>
      </SheetHeader>

      <div class="flex flex-col gap-5 px-4 pb-6">
        <p v-if="detail.description" class="text-sm text-muted-foreground">{{ detail.description }}</p>

        <Callout v-for="(note, index) in detail.notes" :key="index" :tone="note.tone" :title="note.title">{{ note.text }}</Callout>

        <dl v-if="detail.stats?.length" data-slot="evidence-stats" class="grid grid-cols-2 gap-3 lg:grid-cols-4">
          <div v-for="stat in detail.stats" :key="stat.label" class="flex min-w-0 flex-col gap-1 rounded-lg border bg-linear-to-t from-brand/5 to-card p-3">
            <dt class="text-xs text-muted-foreground">{{ stat.label }}</dt>
            <dd class="flex flex-wrap items-center gap-2">
              <span class="text-xl font-semibold tabular-nums" data-slot="evidence-stat-value">{{ stat.value }}</span>
              <StatusBadge v-if="stat.tone && stat.tone !== 'pass'" :tone="stat.tone" />
            </dd>
            <dd v-if="stat.meta" class="text-xs text-muted-foreground">{{ stat.meta }}</dd>
          </div>
        </dl>

        <section v-if="detail.trend && detail.trend.values.length > 1" class="flex flex-col gap-2 rounded-lg border p-4" data-slot="evidence-trend">
          <div class="flex flex-wrap items-baseline justify-between gap-2">
            <h3 class="text-sm font-semibold">{{ detail.trend.title }}</h3>
            <span v-if="detail.trend.meta" class="text-xs text-muted-foreground">{{ detail.trend.meta }}</span>
          </div>
          <Sparkline :values="detail.trend.values" :label="`${detail.trend.title}: ${detail.trend.values.length} points`" />
          <div v-if="detail.trend.range" class="flex justify-between text-xs text-muted-foreground tabular-nums">
            <span>{{ detail.trend.range[0] }}</span><span>{{ detail.trend.range[1] }}</span>
          </div>
        </section>

        <dl v-if="fields.length" data-slot="evidence-fields" class="grid grid-cols-1 gap-x-6 gap-y-2 rounded-lg border p-4 text-sm sm:grid-cols-[minmax(9rem,auto)_1fr]">
          <template v-for="field in fields" :key="field.label">
            <dt class="text-muted-foreground">{{ field.label }}</dt>
            <dd class="font-medium break-words" :class="field.value === '—' && 'text-muted-foreground'" :title="field.hint">{{ field.value }}</dd>
          </template>
        </dl>

        <slot />

        <EmptyState v-if="detail.empty" :title="detail.empty" />

        <section v-for="(table, index) in detail.tables" :key="index" class="flex flex-col gap-2">
          <div v-if="table.title || table.meta" class="flex flex-wrap items-baseline justify-between gap-2">
            <h3 v-if="table.title" class="text-sm font-semibold">{{ table.title }}</h3>
            <span v-if="table.meta" class="text-xs text-muted-foreground">{{ table.meta }}</span>
          </div>
          <DataTable
            :columns="table.columns"
            :rows="table.rows"
            :search-text="table.searchText"
            :searchable="table.rows.length > 10"
            :page-size="table.pageSize ?? 25"
            :empty-text="table.emptyText"
            :label="table.title"
            :on-row-click="table.rowDetail ? (row: unknown) => drillRow(table.rowDetail!, row) : undefined"
            @drill="(d) => emit('drill', d)"
          />
        </section>

        <p v-if="detail.footnote" class="border-t pt-4 text-xs text-muted-foreground" data-slot="evidence-footnote">{{ detail.footnote }}</p>
      </div>
    </SheetContent>
  </Sheet>
</template>
