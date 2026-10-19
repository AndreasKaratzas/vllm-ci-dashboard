import { fileURLToPath, URL } from 'node:url'
import tailwindcss from '@tailwindcss/vite'
import vue from '@vitejs/plugin-vue'
import type { Plugin } from 'vite'
import { defineConfig } from 'vitest/config'

// build_site.py rewrites ?v=<n> in the root index.html on every publication, so
// the stable entry names below still bust browser caches per generation.
function cacheBustEntries(): Plugin {
  return {
    name: 'cache-bust-entries',
    enforce: 'post',
    transformIndexHtml(html) {
      return html.replace(/((?:src|href)="\.\/v2\/(?:assets\/app\.(?:js|css)|theme-init\.js))"/g, '$1?v=1"')
    },
  }
}

// The published site serves data/ beside v2/. In development, proxy it to the
// live Pages deployment so views render against real published snapshots.
const LIVE_SITE = 'https://andreaskaratzas.github.io'

export default defineConfig({
  base: './',
  plugins: [vue(), tailwindcss(), cacheBustEntries()],
  resolve: { alias: { '@': fileURLToPath(new URL('./src', import.meta.url)) } },
  build: {
    // The root page is v2; the classic page lives at docs/classic.html.
    outDir: '../docs',
    emptyOutDir: false,
    chunkSizeWarningLimit: 1200,
    rollupOptions: {
      output: {
        // One stable, cache-busted entry: the site-health monitor and
        // build_site.py address it by name. No lazy chunks, because a chunk
        // importing "./app.js" would load a second copy of "app.js?v=N".
        entryFileNames: 'v2/assets/app.js',
        codeSplitting: false,
        assetFileNames: (info) =>
          info.names.some((name) => name.endsWith('.css')) && info.names.some((name) => name.startsWith('index'))
            ? 'v2/assets/app.css'
            : 'v2/assets/[name]-[hash][extname]',
      },
    },
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
