<script setup lang="ts">
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

// One labelled classic "segmented" control; clicking the active item keeps it.
defineProps<{ label: string; ariaLabel?: string; items: { id: string; label: string }[] }>()
const model = defineModel<string>({ required: true })
</script>

<template>
  <div class="flex flex-col gap-1.5">
    <span class="text-xs font-medium text-muted-foreground">{{ label }}</span>
    <ToggleGroup
      :model-value="model"
      type="single"
      variant="outline"
      size="sm"
      :aria-label="ariaLabel ?? label"
      class="flex-wrap"
      @update:model-value="(v) => v && (model = String(v))"
    >
      <ToggleGroupItem v-for="item in items" :key="item.id" :value="item.id" class="px-2.5">{{ item.label }}</ToggleGroupItem>
    </ToggleGroup>
  </div>
</template>
