<script setup lang="ts">
// Classic detailFields + sourceActions: empty values are omitted.
import { Button } from '@/components/ui/button'

const props = defineProps<{ fields: { label: string; value: unknown }[]; sources?: { label: string; url: string }[] }>()
const shown = () => props.fields.filter((f) => f.value !== null && f.value !== undefined && f.value !== '')
</script>

<template>
  <dl v-if="shown().length" class="grid gap-x-6 gap-y-3 rounded-lg border p-4 text-sm sm:grid-cols-2" data-testid="detail-fields">
    <div v-for="field in shown()" :key="field.label" class="flex min-w-0 flex-col gap-0.5">
      <dt class="text-xs font-medium tracking-wide text-muted-foreground uppercase">{{ field.label }}</dt>
      <dd class="font-medium break-words">{{ field.value }}</dd>
    </div>
  </dl>
  <div v-if="sources?.length" class="flex flex-wrap gap-2">
    <Button v-for="source in sources" :key="source.url + source.label" variant="outline" size="sm" as="a" :href="source.url" target="_blank" rel="noopener">
      {{ source.label }} ↗
    </Button>
  </div>
</template>
