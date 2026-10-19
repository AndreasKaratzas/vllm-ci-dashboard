<script setup lang="ts">
import { ChevronLeft, ChevronRight } from '@lucide/vue'
import { computed, ref, watch } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AMD_CATALOG_FILTERS, amdCatalogRows, passRateLabel, type AmdCatalogFilter, type CatalogRequest } from '@/domain/analytics'
import { amdLatestState, amdStateLabel } from '@/domain/ciHealth'
import { integer, toneForState, value, type Rec } from '@/domain/format'

// Classic openAmdCatalog: search plus the result filter over exact AMD job variants, 50 per page.
const props = defineProps<{ request: CatalogRequest | null; latestBuild: unknown }>()
const emit = defineEmits<{ inspect: [Rec] }>()
const open = defineModel<boolean>('open', { required: true })

const PAGE_SIZE = 50
const search = ref('')
const mode = ref<AmdCatalogFilter>('all')
const page = ref(0)
watch(
  () => props.request,
  (request) => {
    search.value = ''
    mode.value = request?.filter ?? 'all'
    page.value = 0
  },
  { immediate: true },
)
watch([search, mode], () => (page.value = 0))

const filtered = computed(() => amdCatalogRows(props.request?.rows ?? [], props.latestBuild, mode.value, search.value))
const pageCount = computed(() => Math.max(1, Math.ceil(filtered.value.length / PAGE_SIZE)))
const start = computed(() => Math.max(0, Math.min(page.value, pageCount.value - 1)) * PAGE_SIZE)
const visible = computed(() => filtered.value.slice(start.value, start.value + PAGE_SIZE))
const caption = computed(
  () =>
    `${integer(start.value + 1)}-${integer(start.value + visible.value.length)} of ${integer(filtered.value.length)} matching AMD job variants`,
)
const latest = (row: Rec) => amdLatestState(row, props.latestBuild)
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-6xl" data-testid="amd-catalog">
      <SheetHeader>
        <SheetTitle>{{ request?.title }}</SheetTitle>
        <SheetDescription>{{ request?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-3 px-4 pb-6">
        <div class="flex flex-wrap items-center gap-2">
          <Input v-model="search" type="search" placeholder="Search AMD job variant, hardware, or queue" aria-label="Search AMD job variants" class="h-8 max-w-sm" />
          <Select v-model="mode">
            <SelectTrigger size="sm" class="w-48" aria-label="Filter AMD job variants by health"><SelectValue /></SelectTrigger>
            <SelectContent>
              <SelectItem v-for="[id, label] in AMD_CATALOG_FILTERS" :key="id" :value="id">{{ label }}</SelectItem>
            </SelectContent>
          </Select>
          <span class="ml-auto text-sm text-muted-foreground" data-testid="catalog-caption">{{ filtered.length ? caption : 'No matching observations.' }}</span>
        </div>
        <div class="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader class="bg-muted">
              <TableRow>
                <TableHead>AMD job variant</TableHead>
                <TableHead>Latest</TableHead>
                <TableHead class="text-right">Retained-run pass rate</TableHead>
                <TableHead class="text-right">Runs</TableHead>
                <TableHead class="text-right">Pass streak</TableHead>
                <TableHead>Hardware</TableHead>
                <TableHead>Queue</TableHead>
                <TableHead>Evidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="row in visible" :key="row.id || row.name" data-testid="catalog-row">
                <TableCell class="whitespace-normal">
                  <button type="button" class="text-left font-medium hover:underline" @click="emit('inspect', row)">
                    {{ value(row.display_name || row.name || row.job_name) }}
                  </button>
                  <div class="font-mono text-xs text-muted-foreground">
                    {{ value(row.hardware_variant || row.hardware, 'unknown') }} - {{ value(row.queue, 'queue unavailable') }}
                  </div>
                </TableCell>
                <TableCell>
                  <a v-if="latest(row) !== 'missing' && row.latest_url" :href="row.latest_url" target="_blank" rel="noopener">
                    <ToneBadge :tone="toneForState(latest(row))">{{ amdStateLabel(latest(row)) }}</ToneBadge>
                  </a>
                  <button v-else type="button" @click="emit('inspect', row)">
                    <ToneBadge :tone="toneForState(latest(row))">{{ amdStateLabel(latest(row)) }}</ToneBadge>
                  </button>
                </TableCell>
                <TableCell class="text-right tabular-nums">
                  <button type="button" class="hover:underline" @click="emit('inspect', row)">{{ passRateLabel(row) }}</button>
                </TableCell>
                <TableCell class="text-right tabular-nums">
                  <button type="button" class="hover:underline" @click="emit('inspect', row)">{{ integer(row.runs) }}</button>
                </TableCell>
                <TableCell class="text-right tabular-nums">
                  <button type="button" class="hover:underline" @click="emit('inspect', row)">{{ integer(row.current_pass_streak) }}</button>
                </TableCell>
                <TableCell><ToneBadge tone="is-neutral">{{ value(row.hardware_variant || row.hardware) }}</ToneBadge></TableCell>
                <TableCell class="font-mono text-xs">{{ value(row.queue) }}</TableCell>
                <TableCell>
                  <a v-if="row.latest_url" :href="row.latest_url" target="_blank" rel="noopener" class="hover:underline">Latest AMD job ↗</a>
                  <button v-else type="button" class="font-medium hover:underline" @click="emit('inspect', row)">Inspect history</button>
                </TableCell>
              </TableRow>
              <TableRow v-if="!visible.length">
                <TableCell colspan="8" class="h-20 text-center text-muted-foreground">No matching observations.</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <div v-if="filtered.length > PAGE_SIZE" class="flex items-center justify-end gap-2 text-sm text-muted-foreground">
          <span>Page {{ integer(start / PAGE_SIZE + 1) }} of {{ integer(pageCount) }}</span>
          <Button variant="outline" size="icon" class="size-8" :disabled="start === 0" aria-label="Previous page" @click="page = start / PAGE_SIZE - 1">
            <ChevronLeft />
          </Button>
          <Button
            variant="outline"
            size="icon"
            class="size-8"
            :disabled="start / PAGE_SIZE >= pageCount - 1"
            aria-label="Next page"
            @click="page = start / PAGE_SIZE + 1"
          >
            <ChevronRight />
          </Button>
        </div>
      </div>
    </SheetContent>
  </Sheet>
</template>
