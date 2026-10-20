<script setup lang="ts">
import { computed } from 'vue'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

export interface RangeOption {
  /** Number of trailing points to show; 0 shows every point. */
  value: number
  label: string
}

const props = defineProps<{ options: RangeOption[]; label: string }>()
const range = defineModel<number>({ default: 0 })

const model = computed({
  get: () => String(range.value),
  set: (value: string) => {
    if (value) range.value = Number(value)
  },
})
const current = computed(() => props.options.find((option) => option.value === range.value)?.label ?? '')
</script>

<template>
  <ToggleGroup v-model="model" type="single" variant="outline" size="sm" class="hidden sm:flex" :aria-label="label" data-slot="range-toggle">
    <ToggleGroupItem v-for="option in options" :key="option.value" :value="String(option.value)" class="px-3">{{ option.label }}</ToggleGroupItem>
  </ToggleGroup>
  <Select v-model="model">
    <SelectTrigger size="sm" class="w-32 sm:hidden" :aria-label="label">
      <SelectValue>{{ current }}</SelectValue>
    </SelectTrigger>
    <SelectContent>
      <SelectItem v-for="option in options" :key="option.value" :value="String(option.value)">{{ option.label }}</SelectItem>
    </SelectContent>
  </Select>
</template>
