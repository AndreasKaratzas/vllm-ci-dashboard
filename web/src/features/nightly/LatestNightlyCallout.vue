<script setup lang="ts">
import { computed } from 'vue'
import Callout from '@/components/feedback/Callout.vue'
import { Button } from '@/components/ui/button'
import { useClock } from '@/composables/useClock'
import { age, counted } from '@/domain/format'
import type { LatestNightly } from '@/domain/nightly'

// Every page that shows nightly results says the same thing about the newest nightly's state.
const props = defineProps<{ nightly: LatestNightly; onOpen?: () => void }>()

const now = useClock()
const number = computed(() => props.nightly.latest.number)
const kind = computed(() => props.nightly.state.kind)
const failures = computed(() => props.nightly.hard + props.nightly.soft)
const sofar = computed(() =>
  [props.nightly.hard ? `${props.nightly.hard} hard` : '', props.nightly.soft ? `${props.nightly.soft} soft` : ''].filter(Boolean).join(' and '),
)
const title = computed(() => {
  if (kind.value === 'amd-complete') return `Nightly #${number.value}: ${props.nightly.state.detail}`
  if (kind.value === 'awaiting') return `Nightly #${number.value} is still running its AMD jobs`
  if (kind.value === 'retry') return `Nightly #${number.value} is re-running an AMD job`
  return `Nightly #${number.value} has no AMD test results`
})
const signal = computed(() => (props.nightly.signal && props.nightly.signal !== props.nightly.latest ? props.nightly.signal : null))
const openable = computed(() => kind.value !== 'amd-complete' && kind.value !== 'blocked' && failures.value > 0 && !!props.onOpen)
</script>

<template>
  <Callout :tone="nightly.state.tone" :title="title" :data-state="kind" data-testid="latest-nightly">
    <template v-if="kind === 'amd-complete'">Every AMD job is final, so its results count now; the build stays open only for jobs on other hardware.</template>
    <template v-else-if="kind === 'awaiting'">Started {{ age(nightly.latest.created_at, now) }}<template v-if="failures">, with {{ sofar }} {{ failures === 1 ? 'failure' : 'failures' }} so far</template>.</template>
    <template v-else-if="kind === 'retry'">{{ nightly.state.detail }}.</template>
    <template v-else-if="kind === 'blocked'">{{ nightly.state.detail }}.</template>
    <template v-else>It finished without parsed AMD test results<template v-if="failures">; {{ counted(failures, 'job variant') }} failed</template>.</template>
    <template v-if="signal"> Counts come from #{{ signal.number }}, the newest nightly with test results.</template>
    <template v-else-if="!nightly.signal"> No retained nightly has test results yet.</template>
    <template v-if="openable" #action>
      <Button variant="outline" size="sm" @click="onOpen">{{ kind === 'no-results' ? 'Failed jobs' : 'Failures so far' }}</Button>
    </template>
  </Callout>
</template>
