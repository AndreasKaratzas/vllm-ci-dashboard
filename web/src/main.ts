import { leftForClassicPage } from './legacyBoot'
import { createApp } from 'vue'
import App from './App.vue'
import { router } from './router'
import './styles/main.css'

if (!leftForClassicPage) createApp(App).use(router).mount('#app')
