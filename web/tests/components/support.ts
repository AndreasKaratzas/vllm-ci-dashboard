import { flushPromises } from '@vue/test-utils'
import { defineComponent, h, nextTick } from 'vue'
import { createMemoryHistory, createRouter } from 'vue-router'

/** Visible text with element boundaries read as word breaks. */
export function text(el: Element | null | undefined): string {
  if (!el) return ''
  const walker = document.createTreeWalker(el, NodeFilter.SHOW_TEXT)
  const parts: string[] = []
  for (let node = walker.nextNode(); node; node = walker.nextNode()) parts.push(node.textContent ?? '')
  return parts.map((p) => p.trim()).filter(Boolean).join(' ').replace(/\s+/g, ' ')
}

export async function settle() {
  await flushPromises()
  await nextTick()
}

export async function testRouter(path = '/') {
  const Blank = defineComponent({ render: () => h('div') })
  const router = createRouter({
    history: createMemoryHistory(),
    routes: [
      { path: '/', component: Blank },
      { path: '/health', component: Blank },
    ],
  })
  await router.push(path)
  await router.isReady()
  return router
}

/** jsdom lacks the layout APIs Unovis and reka-ui probe on mount. */
export function installLayoutStubs() {
  globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
  } as unknown as typeof ResizeObserver
  Element.prototype.scrollIntoView ??= () => {}
  Element.prototype.hasPointerCapture ??= () => false
  Element.prototype.releasePointerCapture ??= () => {}
  const svg = SVGElement.prototype as SVGElement & { getComputedTextLength?: () => number; getBBox?: () => DOMRect }
  svg.getComputedTextLength ??= () => 0
  svg.getBBox ??= () => new DOMRect()
}
