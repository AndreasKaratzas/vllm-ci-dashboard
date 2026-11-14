<script setup lang="ts">
import { computed, ref } from 'vue'
import BarChart from '@/components/charts/BarChart.vue'
import type { Detail } from '@/components/evidence/detail'
import SegmentedSelect from '@/components/layout/SegmentedSelect.vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { signalNoun, type AgentLoad, type AgentOptions } from '@/domain/agentHealth'
import { integer } from '@/domain/format'
import { STATE_SERIES, loadDetail } from './details'

// Where the window's failures land: the worst queues or test groups, each opening its nodes and runs.
const props = defineProps<{ queues: AgentLoad[]; groups: AgentLoad[]; options: AgentOptions; node: (nodeRaw: string) => Detail | undefined }>()
const emit = defineEmits<{ open: [Detail] }>()

const ROWS = 8
const KINDS = [
  { value: 'queue', label: 'Queue' },
  { value: 'group', label: 'Test group' },
]
const kind = ref<'queue' | 'group'>('queue')
const rows = computed(() => (kind.value === 'queue' ? props.queues : props.groups).slice(0, ROWS))
const noun = computed(() => signalNoun(props.options.signal))
// Group names repeat the AMD prefix of every AMD job; the full name stays in the tooltip and sheet.
const category = (load: AgentLoad) => (kind.value === 'group' ? load.key.replace(/^(AMD:\s*)?:amd:\s*/, '') : load.key)
const rowLabel = (load: AgentLoad) => `${load.key} · ${integer(load.nodes)} ${load.nodes === 1 ? 'node' : 'nodes'}`
</script>

<template>
  <Card class="gap-4" data-testid="agent-load">
    <CardHeader>
      <CardTitle>Failures by {{ kind === 'queue' ? 'queue' : 'test group' }}</CardTitle>
      <CardDescription>
        The {{ rows.length }} {{ kind === 'queue' ? 'Buildkite queues' : 'test groups' }} with the most retained {{ noun }} in {{ options.windowId }}. Select one for its nodes and runs.
      </CardDescription>
      <CardAction>
        <SegmentedSelect v-model="kind" label="Group failures by" :items="KINDS" :show-label="false" data-testid="agent-load-kind" />
      </CardAction>
    </CardHeader>
    <CardContent>
      <BarChart
        :key="kind"
        :rows="rows"
        :category="category"
        :row-label="rowLabel"
        :series="STATE_SERIES"
        orientation="horizontal"
        :category-width="kind === 'group' ? 150 : undefined"
        :label="`Retained ${noun} by ${kind === 'queue' ? 'queue' : 'test group'}`"
        :value-format="integer"
        legend
        inspect-placement="legend"
        :hint="`Click for the ${kind === 'queue' ? 'queue' : 'group'}'s nodes and runs`"
        :data-testid="kind === 'queue' ? 'agent-queues' : 'agent-groups'"
        @select="(load: AgentLoad) => emit('open', loadDetail(kind, load, options, node))"
      />
    </CardContent>
  </Card>
</template>
