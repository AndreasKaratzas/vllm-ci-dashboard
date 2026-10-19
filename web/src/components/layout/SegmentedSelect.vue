<script setup lang="ts">
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

export interface SegmentedItem {
  value: string
  label: string
  /** The longer name shown in the narrow-screen select and as the toggle's tooltip. */
  title?: string
}

// One single-choice filter: a segmented toggle from sm up, a select below sm.
defineProps<{ label: string; items: SegmentedItem[] }>()
const model = defineModel<string>({ required: true })
</script>

<template>
  <div class="flex min-w-0 flex-col gap-1.5" data-slot="segmented-select">
    <span class="text-xs font-medium text-muted-foreground" aria-hidden="true">{{ label }}</span>
    <ToggleGroup
      :model-value="model"
      type="single"
      variant="outline"
      size="sm"
      :aria-label="label"
      class="hidden sm:flex"
      @update:model-value="(value) => value && (model = String(value))"
    >
      <ToggleGroupItem v-for="item in items" :key="item.value" :value="item.value" :title="item.title" class="px-2.5">{{ item.label }}</ToggleGroupItem>
    </ToggleGroup>
    <Select v-model="model">
      <SelectTrigger size="sm" class="w-full sm:hidden" :aria-label="label">
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem v-for="item in items" :key="item.value" :value="item.value">{{ item.title ?? item.label }}</SelectItem>
      </SelectContent>
    </Select>
  </div>
</template>
