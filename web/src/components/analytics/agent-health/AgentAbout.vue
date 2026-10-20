<script setup lang="ts">
import { HURTING_NODE_FAILURES, type AgentModel } from '@/domain/agentHealth'

defineProps<{ model: AgentModel }>()
</script>

<template>
  <p>
    Every terminal attempt of a CI main job on AMD MI GPU hardware, including attempts Buildkite superseded with an automatic retry, is attributed to the
    physical node from the Buildkite agent tag. The timeline and co-failure clustering follow the selected failure signal.
  </p>
  <p v-if="model.completionScopeNote" data-testid="agent-completion-scope">{{ model.completionScopeNote }}</p>
  <p class="font-medium text-foreground">When is a failure the node's fault?</p>
  <p>Each failing attempt is explained by the first rule that matches:</p>
  <ol class="list-decimal pl-5" data-testid="agent-attribution-rules">
    <li>The agent died mid-job: shared infrastructure when agents on {{ model.rules.burstNodes }} or more nodes died within {{ model.rules.burstMinutes }} minutes, otherwise the node.</li>
    <li>The job's containers never started: shared infrastructure.</li>
    <li>The same build failed on {{ model.rules.buildWideNodes }} or more nodes: build-wide.</li>
    <li>The retry failed on another node, the test group failed on other nodes within {{ model.rules.recurrenceHours }} hours, or it passed on no other node: test or code.</li>
    <li>Otherwise it is unexplained, and the node's when unexplained failures hit {{ model.rules.repeatGroups }} or more test groups on it within {{ model.rules.repeatHours }} hours.</li>
  </ol>
  <p>The node-suspect signal keeps the node and unexplained failures. A node is hurting CI once {{ HURTING_NODE_FAILURES }} recent failures are attributed to it.</p>
</template>
