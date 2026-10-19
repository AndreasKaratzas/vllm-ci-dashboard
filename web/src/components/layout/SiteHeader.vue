<script setup lang="ts">
import { Moon, Search, Sun } from '@lucide/vue'
import { computed } from 'vue'
import { useRoute } from 'vue-router'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Separator } from '@/components/ui/separator'
import { SidebarTrigger } from '@/components/ui/sidebar'
import { useOperations } from '@/composables/useOperations'
import { useTheme } from '@/composables/useTheme'
import { age } from '@/domain/format'
import type { NavTab } from '@/router'

defineEmits<{ search: [] }>()
const route = useRoute()
const { ops } = useOperations()
const { dark, toggle } = useTheme()
const title = computed(() => (route.meta.tab as NavTab | undefined)?.label ?? 'Dashboard')
const generatedAt = computed<string | undefined>(() => ops.value?.generated_at)
// Matches the publication banner's three-hour freshness SLO.
const fresh = computed(() => !!generatedAt.value && Date.now() - Date.parse(generatedAt.value) <= 3 * 60 * 60 * 1000)
</script>

<template>
  <header
    class="sticky top-0 z-10 flex h-(--header-height) shrink-0 items-center gap-2 rounded-t-xl border-b bg-background/80 backdrop-blur transition-[width,height] ease-linear"
  >
    <div class="flex w-full items-center gap-1 px-4 lg:gap-2 lg:px-6">
      <SidebarTrigger class="-ml-1" />
      <Separator orientation="vertical" class="mx-2 data-[orientation=vertical]:h-4" />
      <h1 class="text-base font-medium">{{ title }}</h1>
      <div class="ml-auto flex items-center gap-2">
        <Badge v-if="generatedAt" variant="outline" class="hidden text-muted-foreground sm:flex" :title="generatedAt">
          <span class="size-1.5 rounded-full" :class="fresh ? 'bg-success' : 'bg-destructive'" />
          Updated {{ age(generatedAt) }}
        </Badge>
        <Button variant="outline" size="sm" class="text-muted-foreground" aria-label="Search the dashboard" @click="$emit('search')">
          <Search />
          <span class="hidden md:inline">Search builds, issues, PRs…</span>
          <kbd class="pointer-events-none hidden rounded border bg-background px-1.5 font-mono text-[10px] md:inline">⌘K</kbd>
        </Button>
        <Button variant="ghost" size="icon" :aria-label="dark ? 'Switch to light theme' : 'Switch to dark theme'" @click="toggle">
          <Sun v-if="dark" /><Moon v-else />
        </Button>
      </div>
    </div>
  </header>
</template>
