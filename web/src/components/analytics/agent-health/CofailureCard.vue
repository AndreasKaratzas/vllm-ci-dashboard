<script setup lang="ts">
import { computed } from 'vue'
import ToneBadge from '@/components/ops/ToneBadge.vue'
import { Card } from '@/components/ui/card'
import { cofailureCard, type CofailEvent } from '@/domain/agentHealth'

const props = defineProps<{ event: CofailEvent; selected: boolean }>()
const emit = defineEmits<{ select: [scroll: boolean] }>()
const card = computed(() => cofailureCard(props.event, props.selected))

function onCard(e: MouseEvent) {
  // Links and buttons inside the card keep their own action.
  if ((e.target as HTMLElement | null)?.closest?.('a, button')) return
  emit('select', true)
}
</script>

<template>
  <Card
    class="cursor-pointer gap-2 px-4 py-3 transition-colors hover:bg-accent/40"
    :class="[selected ? 'ring-2 ring-primary' : '', card.cross ? 'border-destructive/50' : '']"
    data-testid="cofailure-card"
    :data-selected="selected"
    @click="onCard"
  >
    <div class="flex flex-wrap items-center gap-2">
      <button
        type="button"
        class="font-mono text-sm font-semibold hover:underline"
        title="Zoom the timeline to this co-failure event and expand it"
        data-testid="cofailure-node"
        @click="emit('select', true)"
      >
        {{ card.node }}
      </button>
      <ToneBadge :tone="card.patternTone">{{ card.pattern }}</ToneBadge>
      <ToneBadge v-if="card.cross" tone="hard">cross-pipeline</ToneBadge>
    </div>
    <p class="text-xs text-muted-foreground" data-testid="cofailure-meta">{{ card.meta }}</p>
    <p v-if="card.detail" class="text-xs font-medium" data-testid="cofailure-detail">{{ card.detail }}</p>
    <ul class="flex flex-col gap-1">
      <li v-for="(item, index) in card.runs" :key="index" class="flex flex-wrap items-center gap-x-2 gap-y-0.5 text-xs" data-testid="cofailure-run">
        <span class="size-2.5 shrink-0 rounded-full" :style="{ background: item.color }" :title="item.run.state" />
        <span class="min-w-0 flex-1 truncate font-medium" :title="item.run.group">{{ item.run.group }}</span>
        <span class="text-muted-foreground">{{ item.meta }}</span>
        <a v-if="item.run.url" :href="item.run.url" target="_blank" rel="noopener" class="font-mono text-primary hover:underline">{{ item.link }}</a>
        <span v-else class="font-mono text-muted-foreground">{{ item.link }}</span>
        <span v-if="item.time" class="w-full pl-4 text-muted-foreground">{{ item.time }}</span>
      </li>
    </ul>
    <button v-if="card.more" type="button" class="w-fit text-xs text-primary hover:underline" @click.stop="emit('select', false)">{{ card.more }}</button>
  </Card>
</template>
