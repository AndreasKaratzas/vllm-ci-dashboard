import { mount } from '@vue/test-utils'
import { defineComponent, h, nextTick, type WritableComputedRef } from 'vue'
import { createMemoryHistory, createRouter, type Router } from 'vue-router'
import { beforeEach, describe, expect, it } from 'vitest'
import { useQueryState, type QueryStateOptions } from '@/composables/useQueryState'

let router: Router

beforeEach(async () => {
  router = createRouter({ history: createMemoryHistory(), routes: [{ path: '/:any(.*)*', component: { render: () => null } }] })
  await router.push('/')
  await router.push('/analytics?other=1')
})

async function bind<T extends string>(key: string, options: QueryStateOptions<T>) {
  let state!: WritableComputedRef<T>
  mount(
    defineComponent({
      setup() {
        state = useQueryState(key, options)
        return () => h('div')
      },
    }),
    { global: { plugins: [router] } },
  )
  await router.isReady()
  return state
}

const settled = async () => {
  await nextTick()
  await new Promise((resolve) => setTimeout(resolve))
}

describe('useQueryState', () => {
  it('reads the default when the parameter is absent, empty or not allowed', async () => {
    const view = await bind('view', { default: 'groups', allowed: ['groups', 'latency'] as const })
    expect(view.value).toBe('groups')
    await router.replace('/analytics?view=latency')
    expect(view.value).toBe('latency')
    await router.replace('/analytics?view=bogus')
    expect(view.value).toBe('groups')
    await router.replace('/analytics?view=')
    expect(view.value).toBe('groups')
    await router.replace('/analytics?view=latency&view=groups')
    expect(view.value).toBe('latency')
  })

  it('writes the value, keeps other parameters and omits the default', async () => {
    const view = await bind('view', { default: 'groups', allowed: ['groups', 'latency'] as const })
    view.value = 'latency'
    await settled()
    expect(router.currentRoute.value.fullPath).toBe('/analytics?other=1&view=latency')
    view.value = 'groups'
    await settled()
    expect(router.currentRoute.value.fullPath).toBe('/analytics?other=1')
  })

  it('replaces history by default and pushes when asked', async () => {
    const replaced = await bind<string>('window', { default: '7d' })
    replaced.value = '30d'
    await settled()
    expect(router.currentRoute.value.query.window).toBe('30d')
    const pushed = await bind<string>('model', { default: '', history: 'push' })
    pushed.value = 'llama'
    await settled()
    router.back()
    await settled()
    expect(router.currentRoute.value.query.model).toBeUndefined()
    expect(router.currentRoute.value.query.window).toBe('30d')
    // The window change replaced the entry it was made on, so one more step leaves the page.
    router.back()
    await settled()
    expect(router.currentRoute.value.fullPath).toBe('/')
  })
})
