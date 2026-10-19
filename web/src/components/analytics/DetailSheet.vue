<script setup lang="ts">
import { computed } from 'vue'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'

export interface DetailRequest {
  title: string
  subtitle: string
  description?: string
  fields: [string, unknown][]
  sources: { label: string; url: string }[]
}

// Classic openDetailDrawer / openMetricDetail: a description, non-empty fields, and source links.
const props = defineProps<{ detail: DetailRequest | null }>()
const open = defineModel<boolean>('open', { required: true })
const fields = computed(() => (props.detail?.fields ?? []).filter(([, v]) => v !== null && v !== undefined && v !== ''))
const sources = computed(() => (props.detail?.sources ?? []).filter((s) => s && s.url))
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-xl">
      <SheetHeader>
        <SheetTitle>{{ detail?.title }}</SheetTitle>
        <SheetDescription>{{ detail?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-4 px-4 pb-6">
        <p v-if="detail?.description" class="text-sm">{{ detail.description }}</p>
        <dl v-if="fields.length" class="grid grid-cols-[auto_1fr] gap-x-4 gap-y-2 text-sm">
          <template v-for="[label, val] in fields" :key="label">
            <dt class="text-muted-foreground">{{ label }}</dt>
            <dd class="font-medium tabular-nums">{{ val }}</dd>
          </template>
        </dl>
        <div v-if="sources.length" class="flex flex-wrap gap-3">
          <a v-for="source in sources" :key="source.url" :href="source.url" target="_blank" rel="noopener" class="text-sm font-medium hover:underline">
            {{ source.label }} ↗
          </a>
        </div>
      </div>
    </SheetContent>
  </Sheet>
</template>
