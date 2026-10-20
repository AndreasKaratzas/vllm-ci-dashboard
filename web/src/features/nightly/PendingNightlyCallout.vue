<script setup lang="ts">
import { computed } from 'vue'
import Callout from '@/components/feedback/Callout.vue'
import { Button } from '@/components/ui/button'
import { counted } from '@/domain/format'
import type { PendingNightly } from '@/domain/nightly'

// Every page that counts from the newest nightly with test results says so the same way.
const props = defineProps<{ pending: PendingNightly; onOpen?: () => void }>()

const failures = computed(() => props.pending.hard + props.pending.soft)
const sofar = computed(() =>
  [props.pending.hard ? `${props.pending.hard} hard` : '', props.pending.soft ? `${props.pending.soft} soft` : ''].filter(Boolean).join(' and '),
)
</script>

<template>
  <Callout :title="`Nightly #${pending.latest.number} has no test results ${pending.running ? 'yet' : ''}`.trim()" data-testid="pending-nightly">
    <template v-if="pending.running">Buildkite is still running it<template v-if="failures">, with {{ sofar }} {{ failures === 1 ? 'failure' : 'failures' }} so far</template>.</template>
    <template v-else>It finished without parsed test-group results<template v-if="failures">; {{ counted(failures, 'job variant') }} failed</template>.</template>
    <template v-if="pending.signal"> Counts come from #{{ pending.signal.number }}, the newest nightly with test results.</template>
    <template v-else> No retained nightly has test results yet.</template>
    <template v-if="failures && onOpen" #action>
      <Button variant="outline" size="sm" @click="onOpen">{{ pending.running ? 'Failures so far' : 'Failed jobs' }}</Button>
    </template>
  </Callout>
</template>
