<script setup lang="ts">
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import type { PerfDetail } from '@/domain/perf'

defineProps<{ detail: PerfDetail | null }>()
const open = defineModel<boolean>('open', { required: true })
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-xl">
      <SheetHeader>
        <SheetTitle class="break-all">{{ detail?.title }}</SheetTitle>
        <SheetDescription>{{ detail?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-4 px-4 pb-6">
        <p v-if="detail?.description" class="text-sm text-muted-foreground">{{ detail.description }}</p>
        <dl v-if="detail?.fields.length" class="grid grid-cols-[max-content_minmax(0,1fr)] gap-x-4 gap-y-2 rounded-lg border p-4 text-sm">
          <template v-for="field in detail.fields" :key="field.label">
            <dt class="text-muted-foreground">{{ field.label }}</dt>
            <dd class="font-medium break-all" data-testid="perf-detail-field">{{ field.value }}</dd>
          </template>
        </dl>
        <div v-if="detail?.sources.length" class="flex flex-wrap gap-2">
          <Button v-for="source in detail.sources" :key="source.url" variant="outline" size="sm" as="a" :href="source.url" target="_blank" rel="noopener">
            {{ source.label }} ↗
          </Button>
        </div>
      </div>
    </SheetContent>
  </Sheet>
</template>
