<script setup lang="ts">
// Classic openAmdMirrorDetail: one physical mirror.amd declaration.
import { computed } from 'vue'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import type { Rec } from '@/domain/format'
import { amdMirrorDetail } from '@/domain/mirrors'
import DetailFields from './DetailFields.vue'

const props = defineProps<{ row: Rec | null; inventory: Rec | null }>()
const open = defineModel<boolean>('open', { required: true })
const detail = computed(() => (props.row ? amdMirrorDetail(props.row, props.inventory) : null))
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader>
        <SheetTitle>{{ detail?.title }}</SheetTitle>
        <SheetDescription>{{ detail?.subtitle }}</SheetDescription>
      </SheetHeader>
      <div v-if="detail" class="flex flex-col gap-4 px-4 pb-6" data-testid="mirror-detail">
        <p class="text-sm text-muted-foreground">{{ detail.description }}</p>
        <DetailFields :fields="detail.fields" :sources="detail.sources" />
      </div>
    </SheetContent>
  </Sheet>
</template>
