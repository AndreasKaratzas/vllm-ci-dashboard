<script setup lang="ts">
import { Button } from '@/components/ui/button'

// Placeholder for a view that still lives on the classic page.
defineProps<{ label: string; href: string }>()
</script>

<template>
  <div class="flex flex-col items-start gap-3 rounded-lg border p-6" data-testid="not-ported">
    <p class="text-sm text-muted-foreground">{{ label }} has not moved to the new dashboard yet.</p>
    <Button as="a" :href="href">Open {{ label }} on the classic dashboard</Button>
  </div>
</template>
