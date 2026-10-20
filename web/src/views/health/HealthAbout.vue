<script setup lang="ts">
import { computed } from 'vue'
import { integer, type Rec } from '@/domain/format'
import { currentTestGroupParity } from '@/domain/health'
import { mirrorCommit } from '@/domain/mirrors'
import type { HealthViewId } from './views'

const props = defineProps<{ view: HealthViewId; ops: Rec | null }>()

const parity = computed(() => currentTestGroupParity(props.ops?.test_group_parity))
const paritySummary = computed<Rec>(() => parity.value.summary || {})
const rocmInventory = computed<Rec>(() => (parity.value.rocm_inventory || paritySummary.value.rocm_inventory || {}).main || {})
const commit = computed(() => mirrorCommit((props.ops?.test_group_parity || {}).mirror_inventory))
</script>

<template>
  <template v-if="view === 'overview'">
    <p>Logical test groups are source-aligned identities; exact Buildkite job variants are the per-hardware jobs behind them.</p>
    <p>Hardware-distinct jobs are combined only when they represent the same source-aligned test group. A group passes when it passes on at least one observed route.</p>
    <p>Failure movement compares each nightly with the previous one: new and recurring failures count up, fixes count down.</p>
  </template>
  <template v-else-if="view === 'parity'">
    <p>Coverage counts configured routes in the current main CI definitions, not runtime results.</p>
    <p>Hardware- or backend-specific upstream groups are classified as not targeted and stay outside the parity denominator.</p>
    <p v-if="rocmInventory.logical_groups || rocmInventory.physical_definitions">
      Separate ROCm inventory on main: {{ integer(rocmInventory.logical_groups) }} logical AMD test groups from
      {{ integer(rocmInventory.physical_definitions) }} YAML definitions. These counts are not the upstream-parity numerator.
    </p>
  </template>
  <template v-else-if="view === 'coverage'">
    <p>One fixed health policy: a generic test family passes when any configured AMD architecture passes.</p>
    <p>Explicit MI355-sensitive obligations use their exact MI355 route, so another architecture cannot hide a regression.</p>
    <p>Architecture rows count hardware routes; the best-hardware policy deduplicates them into test groups. No-signal groups stay in the denominator.</p>
  </template>
  <template v-else>
    <p>The dashboard pins vLLM main to one commit and scans every .buildkite/test_areas/*.yaml file.</p>
    <p>One top-level YAML step with a non-empty mirror.amd mapping counts once. Optional and soft-fail mirrors are included; parallelism and shard templates do not multiply the total.</p>
    <p>This physical configuration count is separate from runtime logical groups and from upstream route coverage.</p>
    <p v-if="commit">
      <a :href="`https://github.com/vllm-project/vllm/commit/${commit}`" target="_blank" rel="noopener" class="font-medium text-foreground underline-offset-4 hover:underline">
        Scanned commit {{ String(commit).slice(0, 12) }}
      </a>
    </p>
  </template>
</template>
