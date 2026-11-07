<script setup lang="ts">
import { ScanSearch } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import type { InspectItem } from './chart'

defineProps<{ items: InspectItem[]; label: string }>()
const emit = defineEmits<{ select: [index: number] }>()
</script>

<template>
  <DropdownMenu>
    <DropdownMenuTrigger as-child>
      <Button variant="ghost" size="sm" class="h-7 text-muted-foreground" :aria-label="`Inspect ${label}`" data-slot="chart-inspect">
        <ScanSearch /> Inspect
      </Button>
    </DropdownMenuTrigger>
    <DropdownMenuContent align="end" class="max-h-80 w-64">
      <DropdownMenuLabel class="text-xs text-muted-foreground">{{ label }}</DropdownMenuLabel>
      <DropdownMenuSeparator />
      <DropdownMenuItem v-for="(item, index) in items" :key="index" class="justify-between gap-3" @select="emit('select', index)">
        <span class="truncate">{{ item.label }}</span>
        <span v-if="item.detail" class="shrink-0 font-mono text-xs text-muted-foreground tabular-nums">{{ item.detail }}</span>
      </DropdownMenuItem>
    </DropdownMenuContent>
  </DropdownMenu>
</template>
