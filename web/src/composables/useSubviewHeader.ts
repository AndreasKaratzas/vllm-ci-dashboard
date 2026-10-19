import { getCurrentScope, inject, onScopeDispose, provide, shallowRef, watchEffect, type Component, type InjectionKey, type ShallowRef } from 'vue'
import type { Freshness } from '@/components/layout/PageHeader.vue'

/** What a mounted sub-view contributes to its page's header. */
export interface SubviewHeader {
  freshness?: Freshness
  /** Methodology rendered in the header's About popover. */
  about?: Component
}

const KEY: InjectionKey<ShallowRef<SubviewHeader | null>> = Symbol('subview-header')

/** Page side: the header details of whichever sub-view is mounted, or null. */
export function provideSubviewHeader(): ShallowRef<SubviewHeader | null> {
  const header = shallowRef<SubviewHeader | null>(null)
  provide(KEY, header)
  return header
}

/** Sub-view side: publish header details for as long as the calling component is mounted. */
export function useSubviewHeader(source: () => SubviewHeader): void {
  const header = inject(KEY, null)
  if (!header || !getCurrentScope()) return
  let published: SubviewHeader | null = null
  watchEffect(() => {
    published = source()
    header.value = published
  })
  onScopeDispose(() => {
    if (header.value === published) header.value = null
  })
}
