<script setup lang="ts">
defineProps<{ view: 'groups' | 'latency' | 'nightlies' }>()
</script>

<template>
  <template v-if="view === 'groups'">
    <p>Each exact job variant is one AMD Buildkite gating job on main CI; logical test groups combine the hardware routes of one source-aligned group.</p>
    <p>
      Soft results are raw warning observations, not confirmed incidents until they recur on two distinct completed builds. The latest count is
      current-only; older names stay available as history and are not treated as missing failures.
    </p>
    <p>Upstream results are never used as AMD passes.</p>
  </template>
  <template v-else-if="view === 'latency'">
    <p>Only current CI gating jobs count. Each nightly contributes one wall-completion observation per test group; parallel shards contribute their maximum.</p>
    <p>The metric is the median across up to five observations from the latest five completed main CI nightlies. Missing observations stay unavailable; older builds are not substituted.</p>
  </template>
  <template v-else>
    <p>Every current hard or soft failure is counted once, as new when the preceding observed nightly passed and as recurring when it failed too.</p>
    <p>A previous failure that passes is fixed. Missing or skipped jobs are omitted, and nightlies without a test signal infer no movement.</p>
  </template>
</template>
