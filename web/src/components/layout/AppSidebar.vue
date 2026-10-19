<script setup lang="ts">
import { Activity, BarChart3, Cpu, FolderGit2, Gauge, HeartPulse, House, Layers, Search } from '@lucide/vue'
import type { Component } from 'vue'
import { RouterLink, useRoute } from 'vue-router'
import {
  Sidebar,
  SidebarContent,
  SidebarFooter,
  SidebarGroup,
  SidebarGroupContent,
  SidebarGroupLabel,
  SidebarHeader,
  SidebarMenu,
  SidebarMenuButton,
  SidebarMenuItem,
  SidebarMenuSub,
  SidebarMenuSubButton,
  SidebarMenuSubItem,
} from '@/components/ui/sidebar'
import { NAV_TABS } from '@/router'
import { currentSubView, SUB_VIEWS, subViewQuery } from './navigation'
import { SHORTCUT } from './shortcut'

defineEmits<{ search: [] }>()

const route = useRoute()
const ICONS: Record<string, Component> = {
  home: House,
  health: HeartPulse,
  analytics: BarChart3,
  perf: Gauge,
  omni: Layers,
}
const LINKS = [
  { label: 'Buildkite AMD CI', href: 'https://buildkite.com/vllm/amd-ci', icon: Activity },
  { label: 'Dashboard repository', href: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard', icon: FolderGit2 },
]

// The active page gets a brand bar on its leading edge and a brand-colored icon.
const ACTIVE =
  "relative data-[active=true]:text-sidebar-accent-foreground data-[active=true]:[&>svg]:text-brand data-[active=true]:before:absolute data-[active=true]:before:inset-y-2 data-[active=true]:before:left-0 data-[active=true]:before:w-0.5 data-[active=true]:before:rounded-full data-[active=true]:before:bg-brand data-[active=true]:before:content-[''] group-data-[collapsible=icon]:data-[active=true]:before:hidden"
</script>

<template>
  <Sidebar collapsible="icon" variant="inset">
    <SidebarHeader>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" as-child tooltip="vLLM AMD CI">
            <RouterLink to="/" class="gap-3">
              <div class="flex aspect-square size-8 shrink-0 items-center justify-center rounded-lg bg-amd text-white shadow-sm" aria-hidden="true">
                <Cpu class="size-4" />
              </div>
              <div class="grid flex-1 text-left leading-tight">
                <span class="truncate text-sm font-semibold tracking-tight">vLLM AMD CI</span>
                <span class="truncate text-xs text-muted-foreground">ROCm CI dashboard</span>
              </div>
            </RouterLink>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarHeader>
    <SidebarContent>
      <SidebarGroup>
        <SidebarGroupLabel>Dashboard</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem v-for="tab in NAV_TABS" :key="tab.name">
              <SidebarMenuButton as-child :tooltip="tab.label" :is-active="route.name === tab.name" :class="ACTIVE">
                <RouterLink :to="tab.path" :aria-current="route.name === tab.name ? 'page' : undefined">
                  <component :is="ICONS[tab.name]" />
                  <span>{{ tab.label }}</span>
                </RouterLink>
              </SidebarMenuButton>
              <SidebarMenuSub v-if="route.name === tab.name && SUB_VIEWS[tab.name]">
                <SidebarMenuSubItem v-for="view in SUB_VIEWS[tab.name]!.items" :key="view.value">
                  <SidebarMenuSubButton
                    as-child
                    size="sm"
                    :is-active="currentSubView(tab.name, route.query)?.value === view.value"
                    class="text-muted-foreground data-[active=true]:font-medium data-[active=true]:text-foreground"
                  >
                    <RouterLink :to="{ path: tab.path, query: subViewQuery(tab.name, view.value) }">
                      <span>{{ view.label }}</span>
                    </RouterLink>
                  </SidebarMenuSubButton>
                </SidebarMenuSubItem>
              </SidebarMenuSub>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
      <SidebarGroup class="mt-auto">
        <SidebarGroupLabel>Sources</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem v-for="link in LINKS" :key="link.label">
              <SidebarMenuButton as-child size="sm" :tooltip="link.label">
                <a :href="link.href" target="_blank" rel="noopener">
                  <component :is="link.icon" />
                  <span>{{ link.label }}</span>
                </a>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
    </SidebarContent>
    <SidebarFooter>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton tooltip="Search" variant="outline" class="text-muted-foreground" @click="$emit('search')">
            <Search />
            <span>Search</span>
            <kbd class="pointer-events-none ml-auto rounded border bg-background px-1.5 font-mono text-[10px] text-foreground group-data-[collapsible=icon]:hidden">{{ SHORTCUT }}</kbd>
          </SidebarMenuButton>
        </SidebarMenuItem>
      </SidebarMenu>
    </SidebarFooter>
  </Sidebar>
</template>
