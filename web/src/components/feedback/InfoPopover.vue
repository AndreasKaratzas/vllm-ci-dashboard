<script setup lang="ts">
import { Info } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'

withDefaults(defineProps<{ title?: string; label?: string }>(), { label: 'About this view' })
</script>

<template>
  <Popover>
    <PopoverTrigger as-child>
      <Button variant="ghost" size="icon-sm" class="text-muted-foreground hover:text-foreground" :aria-label="label">
        <Info />
      </Button>
    </PopoverTrigger>
    <PopoverContent align="start" class="w-[min(28rem,calc(100vw-2rem))] text-sm">
      <p v-if="title" class="mb-2 font-medium">{{ title }}</p>
      <div class="flex flex-col gap-2 text-muted-foreground"><slot /></div>
    </PopoverContent>
  </Popover>
</template>
