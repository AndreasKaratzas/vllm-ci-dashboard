<script setup lang="ts">
import { ref } from 'vue'
import { RouterView } from 'vue-router'
import AppSidebar from '@/components/layout/AppSidebar.vue'
import CommandPalette from '@/components/layout/CommandPalette.vue'
import PublicationBanner from '@/components/layout/PublicationBanner.vue'
import SiteHeader from '@/components/layout/SiteHeader.vue'
import { SidebarInset, SidebarProvider } from '@/components/ui/sidebar'

const searchOpen = ref(false)
</script>

<template>
  <SidebarProvider :style="{ '--sidebar-width': 'calc(var(--spacing) * 64)', '--header-height': 'calc(var(--spacing) * 14)' }">
    <AppSidebar @search="searchOpen = true" />
    <SidebarInset class="min-w-0">
      <SiteHeader @search="searchOpen = true" />
      <div class="@container/main flex min-w-0 flex-1 flex-col gap-4 p-4 md:gap-6 md:p-6">
        <PublicationBanner />
        <RouterView />
      </div>
    </SidebarInset>
    <CommandPalette v-model:open="searchOpen" />
  </SidebarProvider>
</template>
