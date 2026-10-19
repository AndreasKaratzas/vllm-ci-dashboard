<script setup lang="ts" generic="T">
import type { ColumnDef } from '@tanstack/vue-table'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import DataTable from './DataTable.vue'

defineProps<{ title: string; subtitle?: string; columns: ColumnDef<T, unknown>[]; rows: T[]; searchPlaceholder?: string }>()
const open = defineModel<boolean>('open', { required: true })
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-5xl">
      <SheetHeader>
        <SheetTitle>{{ title }}</SheetTitle>
        <SheetDescription v-if="subtitle">{{ subtitle }}</SheetDescription>
      </SheetHeader>
      <div class="px-4 pb-6">
        <DataTable :columns="columns" :data="rows" :page-size="25" :search-placeholder="searchPlaceholder" />
        <slot />
      </div>
    </SheetContent>
  </Sheet>
</template>
