<script setup lang="ts">
import { computed } from 'vue'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'

export interface WindowOption {
  value: string
  label: string
}

const props = defineProps<{ options: WindowOption[]; label: string }>()
const model = defineModel<string>({ required: true })

// Reka emits an empty value when the active item is pressed again; the window stays selected.
const selected = computed({
  get: () => model.value,
  set: (value: string) => {
    if (value) model.value = value
  },
})
const text = computed(() => props.options.find((option) => option.value === model.value)?.label ?? '')
</script>

<template>
  <div class="flex items-center" data-slot="window-toggle">
    <ToggleGroup v-model="selected" type="single" variant="outline" size="sm" class="hidden sm:flex" :aria-label="label">
      <ToggleGroupItem v-for="option in options" :key="option.value" :value="option.value" :aria-label="option.label" class="px-2.5">
        {{ option.label }}
      </ToggleGroupItem>
    </ToggleGroup>
    <Select v-model="selected">
      <SelectTrigger size="sm" class="w-32 sm:hidden" :aria-label="label">
        <SelectValue>{{ text }}</SelectValue>
      </SelectTrigger>
      <SelectContent>
        <SelectItem v-for="option in options" :key="option.value" :value="option.value">{{ option.label }}</SelectItem>
      </SelectContent>
    </Select>
  </div>
</template>
