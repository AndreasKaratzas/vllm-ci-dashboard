<script setup lang="ts">
import { Moon, Sun } from 'lucide-vue-next'
import { RouterLink, RouterView } from 'vue-router'
import PublicationBanner from '@/components/ops/PublicationBanner.vue'
import { Button } from '@/components/ui/button'
import { useTheme } from '@/composables/useTheme'
import { NAV_TABS } from '@/router'

const { dark, toggle } = useTheme()
</script>

<template>
  <div class="min-h-screen md:grid md:grid-cols-[14rem_minmax(0,1fr)]">
    <aside class="border-b bg-card md:sticky md:top-0 md:h-screen md:border-r md:border-b-0">
      <div class="flex items-center justify-between gap-2 p-4">
        <div>
          <p class="text-xs font-semibold tracking-wide text-amd uppercase">vLLM</p>
          <p class="font-semibold">AMD CI Operations</p>
        </div>
        <Button variant="ghost" size="icon" :aria-label="dark ? 'Switch to light theme' : 'Switch to dark theme'" @click="toggle">
          <Sun v-if="dark" /><Moon v-else />
        </Button>
      </div>
      <nav class="flex gap-1 overflow-x-auto px-2 pb-2 md:flex-col" aria-label="Dashboard sections">
        <RouterLink
          v-for="tab in NAV_TABS"
          :key="tab.name"
          :to="tab.path"
          class="rounded-md px-3 py-2 text-sm whitespace-nowrap text-muted-foreground hover:bg-accent hover:text-accent-foreground"
          exact-active-class="bg-accent font-medium text-accent-foreground"
        >
          {{ tab.label }}
        </RouterLink>
      </nav>
      <p class="hidden px-4 py-2 text-xs text-muted-foreground md:block">
        <a href="../" class="hover:underline">Classic dashboard</a>
      </p>
    </aside>
    <main class="flex min-w-0 flex-col gap-4 p-4 md:p-6">
      <PublicationBanner />
      <RouterView />
    </main>
  </div>
</template>
