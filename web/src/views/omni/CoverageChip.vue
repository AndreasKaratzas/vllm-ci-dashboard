<script setup lang="ts">
import { computed } from 'vue'
import { Button } from '@/components/ui/button'
import { Popover, PopoverContent, PopoverTrigger } from '@/components/ui/popover'
import type { OmniDetail, OmniView } from '@/domain/omni'
import { toneClasses } from '@/lib/palette'

const props = defineProps<{ view: OmniView }>()
const emit = defineEmits<{ open: [OmniDetail] }>()

const note = computed(() => props.view.scopeNote)
const complete = computed(() => Boolean(props.view.mappingView.retainedComplete))
</script>

<template>
  <Popover>
    <PopoverTrigger as-child>
      <Button variant="outline" size="sm" class="h-8 gap-1.5 px-2.5 font-normal text-muted-foreground" data-testid="omni-scope-note" :data-tone="note.tone">
        <span class="size-2 rounded-full" :class="toneClasses(complete ? 'pass' : 'soft', 'fill')" aria-hidden="true" />
        {{ complete ? 'Complete coverage' : 'Partial coverage' }}
      </Button>
    </PopoverTrigger>
    <PopoverContent align="end" class="flex w-[min(26rem,calc(100vw-2rem))] flex-col gap-2 text-sm">
      <p class="font-medium" data-testid="omni-scope-heading">
        {{ note.heading.trim() }}
      </p>
      <p class="text-muted-foreground" data-testid="omni-scope-text">
        {{ note.text }}
      </p>
      <div class="flex flex-wrap gap-2 pt-1">
        <Button variant="outline" size="sm" @click="emit('open', note.methodology())">Inspect methodology</Button>
        <Button v-if="note.excluded" variant="outline" size="sm" @click="emit('open', note.excluded!())">Inspect excluded stale jobs</Button>
      </div>
    </PopoverContent>
  </Popover>
</template>
