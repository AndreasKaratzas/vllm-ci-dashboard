<script setup lang="ts">
import { computed } from 'vue'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { LATENCY_ASSET, latencyEvidence, type LatencyComparison } from '@/domain/analytics'
import type { Rec } from '@/domain/format'

// Classic openLatencyEvidence: every cohort nightly with the exact MI jobs behind its observation.
const props = defineProps<{ row: Rec | null; comparison: LatencyComparison }>()
const open = defineModel<boolean>('open', { required: true })
const evidence = computed(() => (props.row ? latencyEvidence(props.row, props.comparison) : null))
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-4xl" data-testid="latency-evidence">
      <SheetHeader>
        <SheetTitle>{{ evidence?.title }}</SheetTitle>
        <SheetDescription>Recent main CI AMD MI wall completion with exact nightly evidence</SheetDescription>
      </SheetHeader>
      <div v-if="evidence" class="flex flex-col gap-4 px-4 pb-6">
        <div class="rounded-lg border p-4">
          <p class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">AMD MI median wall time</p>
          <p class="text-2xl font-semibold tabular-nums" data-testid="latency-evidence-value">{{ evidence.value }}</p>
          <p class="text-sm text-muted-foreground" data-testid="latency-evidence-meta">{{ evidence.meta }}</p>
        </div>
        <p class="text-sm text-muted-foreground">
          Each nightly contributes one observation: the longest wall completion time across that AMD MI test group’s parallel shards. The
          displayed metric is the median across observed nightlies in the fixed cohort below. Missing observations remain unavailable.
        </p>
        <div>
          <h3 class="text-sm font-semibold">AMD MI observations</h3>
          <p class="text-xs text-muted-foreground">The globally selected main CI nightlies; no older backfill</p>
        </div>
        <div class="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader class="bg-muted">
              <TableRow>
                <TableHead>Main CI nightly</TableHead>
                <TableHead>Nightly created (UTC)</TableHead>
                <TableHead class="text-right">Group wall completion</TableHead>
                <TableHead>Exact MI jobs</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="item in evidence.rows" :key="String(item.number)" data-testid="latency-evidence-row">
                <TableCell>
                  <a v-if="item.buildUrl" :href="item.buildUrl" target="_blank" rel="noopener" class="font-mono hover:underline">#{{ item.number }}</a>
                  <span v-else class="font-mono">#{{ item.number }}</span>
                </TableCell>
                <TableCell>{{ item.created }}</TableCell>
                <TableCell class="text-right tabular-nums">{{ item.wall }}</TableCell>
                <TableCell class="whitespace-normal">
                  <span v-if="!item.observed" class="text-muted-foreground">No observation in this nightly</span>
                  <span v-else-if="!item.jobs.length" class="text-muted-foreground">Exact job links unavailable</span>
                  <div v-else class="flex flex-wrap gap-x-3 gap-y-1">
                    <a v-for="job in item.jobs" :key="job.url" :href="job.url" target="_blank" rel="noopener" class="hover:underline">{{ job.label }}</a>
                  </div>
                </TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <a :href="LATENCY_ASSET" target="_blank" rel="noopener" class="text-sm font-medium hover:underline">Open current latency data ↗</a>
      </div>
    </SheetContent>
  </Sheet>
</template>
