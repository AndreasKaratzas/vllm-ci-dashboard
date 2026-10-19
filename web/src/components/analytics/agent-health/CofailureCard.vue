<script setup lang="ts">
import { ChevronRight, ExternalLink } from '@lucide/vue'
import { computed } from 'vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import { Card } from '@/components/ui/card'
import { cofailureCard, type CofailEvent } from '@/domain/agentHealth'
import { toneClasses } from '@/lib/palette'
import { cn } from '@/lib/utils'
import { runTone, stateLabel } from './details'

const props = defineProps<{ event: CofailEvent; selected: boolean }>()
defineEmits<{ open: [] }>()
const card = computed(() => cofailureCard(props.event, false))
</script>

<template>
  <Card
    :class="cn(
      'group/event relative gap-2 px-4 py-3 transition-[border-color,box-shadow,transform] duration-150 hover:border-foreground/20 hover:shadow-sm has-[[data-event-open]:focus-visible]:ring-3 has-[[data-event-open]:focus-visible]:ring-ring/50 motion-safe:active:scale-[.99]',
      selected && 'border-brand/60 ring-2 ring-brand/30',
    )"
    data-testid="cofailure-card"
    :data-selected="selected"
  >
    <div class="flex min-w-0 items-center gap-2">
      <button
        type="button"
        class="min-w-0 truncate text-left font-mono text-sm font-semibold outline-none after:absolute after:inset-0 after:rounded-xl after:content-['']"
        :aria-label="`${card.node}: open this co-failure event and zoom the timeline to it`"
        data-event-open
        data-testid="cofailure-node"
        @click="$emit('open')"
      >{{ card.node }}</button>
      <ChevronRight class="ml-auto size-4 shrink-0 text-muted-foreground transition-transform group-hover/event:translate-x-0.5 group-hover/event:text-foreground" aria-hidden="true" />
    </div>
    <div class="flex flex-wrap items-center gap-1.5">
      <StatusBadge :tone="card.patternTone" :label="card.pattern" />
      <StatusBadge v-if="card.cross" tone="hard" label="cross-pipeline" />
      <span class="text-xs text-muted-foreground" data-testid="cofailure-meta">{{ card.meta }}</span>
    </div>
    <ul class="flex flex-col gap-1">
      <li v-for="(item, index) in card.runs" :key="index" class="flex min-w-0 items-center gap-2 text-xs" data-testid="cofailure-run">
        <span class="size-2 shrink-0 rounded-full" :class="toneClasses(runTone(item.run.state), 'fill')" aria-hidden="true" />
        <span class="sr-only">{{ stateLabel(item.run.state) }}:</span>
        <span class="min-w-0 flex-1 truncate" :title="item.run.group">{{ item.run.group }}</span>
        <a
          v-if="item.run.url"
          :href="item.run.url"
          target="_blank"
          rel="noopener"
          class="relative z-10 inline-flex shrink-0 items-center gap-1 rounded-sm font-mono text-muted-foreground underline-offset-4 hover:text-foreground hover:underline"
        >#{{ item.run.build_number }}<span class="sr-only"> Buildkite log</span> <ExternalLink class="size-3" aria-hidden="true" /></a>
      </li>
    </ul>
    <p v-if="event.runs.length > card.runs.length" class="text-xs text-muted-foreground">+{{ event.runs.length - card.runs.length }} more runs</p>
  </Card>
</template>
