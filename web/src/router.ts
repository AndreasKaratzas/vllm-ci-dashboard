import { createRouter, createWebHashHistory } from 'vue-router'
import HomeView from './views/HomeView.vue'

const HealthView = () => import('./views/HealthView.vue')

export interface NavTab {
  path: string
  name: string
  label: string
  classicTab: string
}

// classicTab is the matching #tab on the v1 dashboard, used until a view is ported.
export const NAV_TABS: NavTab[] = [
  { path: '/', name: 'home', label: 'Home', classicTab: 'projects' },
  { path: '/health', name: 'health', label: 'CI Health', classicTab: 'ci-health' },
  { path: '/analytics', name: 'analytics', label: 'CI Analytics', classicTab: 'ci-analytics' },
  { path: '/perf', name: 'perf', label: 'Perf Eval', classicTab: 'ci-perf-eval' },
  { path: '/omni', name: 'omni', label: 'Omni CI', classicTab: 'ci-omni' },
]

const NotPortedView = () => import('./views/NotPortedView.vue')

export const router = createRouter({
  history: createWebHashHistory(),
  routes: [
    ...NAV_TABS.map((tab) => ({
      path: tab.path,
      name: tab.name,
      component: tab.name === 'home' ? HomeView : tab.name === 'health' ? HealthView : NotPortedView,
      meta: { tab },
    })),
    { path: '/:pathMatch(.*)*', redirect: '/' },
  ],
})
