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
  classicTab: string
}

// classicTab is the matching #tab on classic.html.
export const NAV_TABS: NavTab[] = [
  { path: '/', name: 'home', label: 'Home', classicTab: 'projects' },
  { path: '/health', name: 'health', label: 'CI Health', classicTab: 'ci-health' },
  { path: '/analytics', name: 'analytics', label: 'CI Analytics', classicTab: 'ci-analytics' },
  { path: '/perf', name: 'perf', label: 'Perf Eval', classicTab: 'ci-perf-eval' },
  { path: '/omni', name: 'omni', label: 'Omni CI', classicTab: 'ci-omni' },
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
