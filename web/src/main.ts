import { leftForOldPage } from './app/redirect'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './styles/main.css'

if (!leftForOldPage) createApp(App).use(router).mount('#app')
