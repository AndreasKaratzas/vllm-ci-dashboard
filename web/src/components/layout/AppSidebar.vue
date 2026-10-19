<script setup lang="ts">
import { Activity, BarChart3, BookOpen, Cpu, ExternalLink, Gauge, HeartPulse, House, Layers } from '@lucide/vue'
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
  SidebarRail,
} from '@/components/ui/sidebar'
import { NAV_TABS } from '@/router'

const route = useRoute()
const ICONS: Record<string, Component> = {
  home: House,
  health: HeartPulse,
  analytics: BarChart3,
  perf: Gauge,
  omni: Layers,
}
const LINKS = [
  { label: 'Classic dashboard', href: 'classic.html', icon: BookOpen },
  { label: 'Buildkite AMD CI', href: 'https://buildkite.com/vllm/amd-ci', icon: Activity },
  { label: 'Dashboard repository', href: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard', icon: ExternalLink },
]
</script>

<template>
  <Sidebar collapsible="icon" variant="inset">
    <SidebarHeader>
      <SidebarMenu>
        <SidebarMenuItem>
          <SidebarMenuButton size="lg" as-child>
            <RouterLink to="/">
              <div class="flex aspect-square size-8 items-center justify-center rounded-lg bg-amd text-white">
                <Cpu class="size-4" />
              </div>
              <div class="grid flex-1 text-left text-sm leading-tight">
                <span class="truncate font-semibold">vLLM AMD CI</span>
                <span class="truncate text-xs text-muted-foreground">Operations</span>
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
              <SidebarMenuButton as-child :tooltip="tab.label" :is-active="route.name === tab.name">
                <RouterLink :to="tab.path">
                  <component :is="ICONS[tab.name]" />
                  <span>{{ tab.label }}</span>
                </RouterLink>
              </SidebarMenuButton>
            </SidebarMenuItem>
          </SidebarMenu>
        </SidebarGroupContent>
      </SidebarGroup>
      <SidebarGroup class="mt-auto">
        <SidebarGroupLabel>Links</SidebarGroupLabel>
        <SidebarGroupContent>
          <SidebarMenu>
            <SidebarMenuItem v-for="link in LINKS" :key="link.label">
              <SidebarMenuButton as-child size="sm" :tooltip="link.label">
                <a :href="link.href" :target="link.href.startsWith('http') ? '_blank' : undefined" rel="noopener">
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
      <p class="px-2 text-xs text-muted-foreground group-data-[collapsible=icon]:hidden">Press <kbd class="font-mono">⌘K</kbd> to search</p>
    </SidebarFooter>
    <SidebarRail />
  </Sidebar>
</template>
