import { leftForOldPage } from './app/redirect'
import { createApp } from 'vue'
import App from './App.vue'
import { trackViews, VIEW_COUNTER } from './data/viewCounter'
import { router } from './router'
import './styles/main.css'

if (!leftForOldPage) {
  if (VIEW_COUNTER) trackViews(router, VIEW_COUNTER)
  createApp(App).use(router).mount('#app')
}
