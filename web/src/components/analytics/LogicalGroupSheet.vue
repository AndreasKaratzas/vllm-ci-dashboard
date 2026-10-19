<script setup lang="ts">
import { computed } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { AMD_TEST_HEALTH_ASSET } from '@/domain/analytics'
import {
  amdLatestState,
  amdLogicalCatalogGroup,
  amdLogicalSignalLabel,
  amdLogicalSignalTone,
  amdLogicalStateLabel,
  amdLogicalStateTone,
  amdStateLabel,
} from '@/domain/ciHealth'
import { integer, toneForState, type Rec, type Tone } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'

// Classic openAmdLogicalGroupDetail: route summary plus every exact job variant.
const props = defineProps<{ row: Rec | null; inventory: Rec; amdHealth: Rec }>()
const emit = defineEmits<{ inspect: [Rec] }>()
const open = defineModel<boolean>('open', { required: true })

const variants = computed<Rec[]>(() => (Array.isArray(props.row?.job_variants) ? props.row!.job_variants : []))
const stats = computed(() => {
  const row = props.row ?? {}
  const inv = props.inventory ?? {}
  const states: Rec[] = Array.isArray(row.hardware_states) ? row.hardware_states : []
  return [
    {
      label: 'Logical group result',
      value: amdLogicalStateLabel(row.state),
      meta: row.state === 'partial' ? 'At least one hardware route passes and at least one does not' : 'Build-pinned route aggregation',
      tone: amdLogicalStateTone(row.state),
    },
    {
      label: 'Hardware routes',
      value: integer(row.hardware_count),
      meta: states.map((s) => `${hardwareDisplayLabel(s.hardware)}: ${amdLogicalSignalLabel(s.state).toLowerCase()}`).join(' · '),
      tone: null,
    },
    { label: 'Exact job variants', value: integer(row.job_variant_count), meta: 'Every variant is listed below', tone: null },
    {
      label: 'Observed build',
      value: inv.build_number ? `#${integer(inv.build_number)}` : '-',
      meta: inv.route_map_aligned ? 'Definitions aligned to the observed commit' : 'Definition alignment unavailable',
      tone: (inv.route_map_aligned ? 'pass' : 'soft') as Tone,
    },
  ]
})
const catalogGroup = (variant: Rec) => amdLogicalCatalogGroup(variant, props.amdHealth)
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-5xl">
      <SheetHeader>
        <SheetTitle>{{ row?.label || row?.logical_key || 'AMD logical test group' }}</SheetTitle>
        <SheetDescription>
          {{ amdLogicalStateLabel(row?.state) }} · {{ integer(row?.hardware_count) }} hardware routes ·
          {{ integer(row?.job_variant_count) }} exact job variants
        </SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-4 px-4 pb-6">
        <div class="grid gap-3 sm:grid-cols-2 lg:grid-cols-4">
          <div v-for="stat in stats" :key="stat.label" class="rounded-lg border p-3">
            <p class="text-xs font-semibold tracking-wide text-muted-foreground uppercase">{{ stat.label }}</p>
            <p class="mt-1 text-lg font-semibold">
              <ToneBadge v-if="stat.tone" :tone="stat.tone">{{ stat.value }}</ToneBadge>
              <template v-else>{{ stat.value }}</template>
            </p>
            <p class="text-xs text-muted-foreground">{{ stat.meta }}</p>
          </div>
        </div>
        <div>
          <h3 class="text-sm font-semibold">Hardware routes and exact jobs</h3>
          <p class="text-xs text-muted-foreground">The logical result follows the published any-route passing policy; exact jobs remain separate evidence.</p>
        </div>
        <div class="overflow-x-auto rounded-lg border">
          <Table>
            <TableHeader class="bg-muted">
              <TableRow>
                <TableHead>Exact AMD job variant</TableHead>
                <TableHead>Hardware route</TableHead>
                <TableHead>Test signal</TableHead>
                <TableHead>Terminal job</TableHead>
                <TableHead class="text-right">Tests passed</TableHead>
                <TableHead>Evidence</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="(variant, index) in variants" :key="variant.id || index">
                <TableCell class="whitespace-normal">
                  <button v-if="catalogGroup(variant)" type="button" class="text-left font-medium hover:underline" @click="emit('inspect', catalogGroup(variant)!)">
                    {{ variant.display_name || variant.exact_job_name || 'Unnamed variant' }}
                  </button>
                  <a v-else-if="variant.job_url" :href="variant.job_url" target="_blank" rel="noopener" class="font-medium hover:underline">
                    {{ variant.display_name || variant.exact_job_name || 'Unnamed variant' }} ↗
                  </a>
                  <span v-else class="font-medium">{{ variant.display_name || variant.exact_job_name || 'Unnamed variant' }}</span>
                </TableCell>
                <TableCell><ToneBadge tone="neutral">{{ hardwareDisplayLabel(variant.hardware_variant || variant.hardware) }}</ToneBadge></TableCell>
                <TableCell>
                  <ToneBadge :tone="amdLogicalSignalTone(variant.test_signal_state)">{{ amdLogicalSignalLabel(variant.test_signal_state) }}</ToneBadge>
                </TableCell>
                <TableCell>
                  <ToneBadge :tone="toneForState(variant.terminal_state)">{{ amdStateLabel(amdLatestState({ latest_state: variant.terminal_state }, null)) }}</ToneBadge>
                </TableCell>
                <TableCell class="text-right tabular-nums">{{ integer(variant.passed_tests) }} / {{ integer(variant.tests) }}</TableCell>
                <TableCell>
                  <a v-if="variant.job_url || variant.build_url" :href="variant.job_url || variant.build_url" target="_blank" rel="noopener" class="hover:underline">Open exact job ↗</a>
                  <span v-else class="text-muted-foreground">Unavailable</span>
                </TableCell>
              </TableRow>
              <TableRow v-if="!variants.length">
                <TableCell colspan="6" class="h-16 text-center text-muted-foreground">No matching observations.</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </div>
        <div class="flex flex-wrap gap-3 text-sm font-medium">
          <a v-if="inventory?.build_url" :href="inventory.build_url" target="_blank" rel="noopener" class="hover:underline">Open observed AMD build ↗</a>
          <a :href="AMD_TEST_HEALTH_ASSET" target="_blank" rel="noopener" class="hover:underline">Open published AMD health data ↗</a>
        </div>
      </div>
    </SheetContent>
  </Sheet>
</template>
