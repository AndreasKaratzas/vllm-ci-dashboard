import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import { defineConfig } from 'vitest/config'

// The published site serves data/ beside v2/. In development, proxy it to the
// live Pages deployment so views render against real published snapshots.
const LIVE_SITE = 'https://andreaskaratzas.github.io'

export default defineConfig({
  base: './',
  plugins: [vue(), tailwindcss()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    outDir: '../docs/v2',
    emptyOutDir: true,
    chunkSizeWarningLimit: 1200,
  },
  server: {
    proxy: {
      '/data': {
        target: LIVE_SITE,
        changeOrigin: true,
        rewrite: (path) => `/vllm-ci-dashboard${path}`,
      },
    },
  },
  test: {
    environment: 'jsdom',
    include: ['tests/**/*.test.ts'],
  },
})
