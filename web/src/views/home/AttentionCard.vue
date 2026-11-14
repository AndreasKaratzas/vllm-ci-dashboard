<script setup lang="ts">
import { ChevronRight, CircleCheck } from '@lucide/vue'
import EmptyState from '@/components/feedback/EmptyState.vue'
import StatusBadge from '@/components/feedback/StatusBadge.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, toneForState, type Rec } from '@/domain/format'
import { attentionEvidence, attentionLabel } from '@/domain/health'

defineProps<{ items: Rec[] }>()
const emit = defineEmits<{ inspect: [Rec] }>()

const severity = (item: Rec) => {
  const text = String(item.severity || 'unknown')
  return text.charAt(0).toUpperCase() + text.slice(1)
}
</script>

<template>
  <Card id="needs-attention" class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="attention">
    <CardHeader>
      <CardTitle class="text-base">Needs attention</CardTitle>
      <CardDescription>{{ items.length ? `${items.length} active signals · open one for its evidence` : 'Nothing needs attention right now' }}</CardDescription>
    </CardHeader>
    <CardContent>
      <EmptyState v-if="!items.length" :icon="CircleCheck" title="All clear" description="No AMD nightly or route signal is active." />
      <ul v-else class="flex flex-col divide-y rounded-lg border">
        <li v-for="item in items" :key="item.kind">
          <button
            type="button"
            class="group/item flex w-full items-center gap-3 px-3 py-3 text-left transition-colors first:rounded-t-lg last:rounded-b-lg hover:bg-muted/50 focus-visible:bg-muted/50 focus-visible:ring-3 focus-visible:ring-ring/50 focus-visible:outline-none active:bg-accent/80"
            data-testid="attention-row"
            @click="emit('inspect', item)"
          >
            <span class="flex min-w-0 flex-1 flex-col gap-1.5">
              <span class="text-sm font-medium">{{ attentionLabel(item) }}</span>
              <span v-if="attentionEvidence(item)" class="text-xs text-muted-foreground" data-testid="attention-evidence">{{ attentionEvidence(item) }}</span>
              <StatusBadge :tone="toneForState(item.severity)" :label="severity(item)" class="w-fit" />
            </span>
            <span class="text-2xl font-semibold tabular-nums">{{ integer(item.count) }}</span>
            <ChevronRight
              class="size-4 shrink-0 text-muted-foreground transition-transform group-hover/item:translate-x-0.5 group-hover/item:text-foreground"
              aria-hidden="true"
            />
          </button>
        </li>
      </ul>
    </CardContent>
  </Card>
</template>
