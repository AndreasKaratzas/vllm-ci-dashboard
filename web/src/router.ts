import { createRouter, createWebHashHistory } from 'vue-router'
import AnalyticsView from './views/AnalyticsView.vue'
import HealthView from './views/HealthView.vue'
import HomeView from './views/HomeView.vue'
import OmniView from './views/OmniView.vue'
import PerfView from './views/PerfView.vue'

export interface NavTab {
  path: string
  name: string
  label: string
}

export const NAV_TABS: NavTab[] = [
  { path: '/', name: 'home', label: 'Home' },
  { path: '/health', name: 'health', label: 'CI Health' },
  { path: '/analytics', name: 'analytics', label: 'CI Analytics' },
  { path: '/perf', name: 'perf', label: 'Perf Eval' },
  { path: '/omni', name: 'omni', label: 'Omni CI' },
]

const VIEWS = { home: HomeView, health: HealthView, analytics: AnalyticsView, perf: PerfView, omni: OmniView }

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    ...NAV_TABS.map((tab) => ({
      path: tab.path,
      name: tab.name,
      component: VIEWS[tab.name as keyof typeof VIEWS],
      meta: { tab },
    })),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
