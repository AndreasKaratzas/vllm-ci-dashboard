<script setup lang="ts">
import { ChevronRight, Moon, Search, Sun } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import SheetStack from '@/components/evidence/SheetStack.vue'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useTheme } from '@/composables/useTheme'
import type { FreshnessSummary } from '@/domain/freshness'
import { FRESHNESS_LABEL, FRESHNESS_TONE, sourceAge } from '@/features/freshness/freshness'
import { useFreshness } from '@/features/freshness/useFreshness'
import { toneClasses } from '@/lib/palette'
import type { NavTab } from '@/router'
import { currentSubView } from './navigation'
import { SHORTCUT } from './shortcut'

defineEmits<{ search: [] }>()
const route = useRoute()
const { dark, toggle } = useTheme()
const freshness = useFreshness()

const tab = computed(() => route.meta.tab as NavTab | undefined)
const subView = computed(() => currentSubView(route.name, route.query))

interface Chip {
  key: string
  label: string
  summary: FreshnessSummary
}
const chips = computed<Chip[]>(() =>
  [
    { key: 'core', label: 'Core data', summary: freshness.core.value },
    { key: 'queue', label: 'Queue', summary: freshness.queue.value },
  ].filter((chip): chip is Chip => chip.summary !== null),
)
const age = (summary: Pick<FreshnessSummary, 'timestamp'>) => sourceAge(summary, freshness.now.value)
</script>

<template>
  <header
    class="sticky top-0 z-10 flex h-(--header-height) shrink-0 items-center gap-2 rounded-t-xl border-b bg-background/80 backdrop-blur transition-[width,height] ease-linear"
  >
    <div class="flex w-full min-w-0 items-center gap-1 px-3 sm:px-4 lg:gap-2 lg:px-6">
      <SidebarTrigger class="-ml-1" />
      <Separator orientation="vertical" class="mx-1 data-[orientation=vertical]:h-4 sm:mx-2" />
      <nav aria-label="Breadcrumb" class="min-w-0">
        <ol class="flex min-w-0 items-center gap-1.5 text-sm">
          <template v-if="tab && subView">
            <li class="hidden sm:block">
              <RouterLink :to="tab.path" class="text-muted-foreground transition-colors hover:text-foreground">{{ tab.label }}</RouterLink>
            </li>
            <li class="hidden text-muted-foreground sm:block" aria-hidden="true"><ChevronRight class="size-3.5" /></li>
          </template>
          <li class="min-w-0 truncate font-medium" aria-current="page">{{ subView?.label ?? tab?.label ?? 'Dashboard' }}</li>
        </ol>
      </nav>
      <div class="ml-auto flex shrink-0 items-center gap-1.5 sm:gap-2">
        <Tooltip v-for="chip in chips" :key="chip.key">
          <TooltipTrigger as-child>
            <Button
              variant="outline"
              size="sm"
              class="h-8 gap-1.5 px-2 font-normal text-muted-foreground sm:px-2.5"
              data-slot="header-freshness"
              :data-freshness="chip.summary.state"
              @click="freshness.open"
            >
              <span class="size-2 shrink-0 rounded-full" :class="toneClasses(FRESHNESS_TONE[chip.summary.state], 'fill')" aria-hidden="true" />
              <span class="hidden lg:inline">{{ chip.label }} {{ age(chip.summary) }}</span>
              <span class="sr-only lg:hidden">{{ chip.label }} {{ age(chip.summary) }}</span>
              <span class="sr-only">: {{ FRESHNESS_LABEL[chip.summary.state] }}</span>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom" align="end" class="max-w-72 text-left">
            <p class="mb-1 font-medium">{{ chip.label }} · {{ FRESHNESS_LABEL[chip.summary.state] }}</p>
            <ul class="grid gap-0.5">
              <li v-for="source in chip.summary.sources" :key="source.key" class="flex justify-between gap-4">
                <span>{{ source.label }}</span>
                <span class="tabular-nums opacity-80">{{ source.timestamp ? age(source) : 'no timestamp' }}</span>
              </li>
            </ul>
            <p class="mt-1 opacity-80">Click for every source.</p>
          </TooltipContent>
        </Tooltip>
        <Button variant="outline" size="sm" class="h-8 text-muted-foreground" @click="$emit('search')">
          <Search />
          <span class="sr-only md:not-sr-only">Search…</span>
          <kbd class="pointer-events-none hidden rounded border bg-background px-1.5 font-mono text-[10px] text-foreground md:inline">{{ SHORTCUT }}</kbd>
        </Button>
        <Button variant="ghost" size="icon" class="size-8" :aria-label="dark ? 'Switch to light theme' : 'Switch to dark theme'" @click="toggle">
          <Sun v-if="dark" /><Moon v-else />
        </Button>
      </div>
    </div>
    <SheetStack :sheets="freshness.sheets" />
  </header>
</template>
