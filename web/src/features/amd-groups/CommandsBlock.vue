<script setup lang="ts">
defineProps<{ text: string }>()
</script>

<template>
  <pre class="max-h-80 overflow-auto rounded-lg border bg-muted/50 p-3 font-mono text-xs whitespace-pre-wrap" tabindex="0">{{ text }}</pre>
</template>
