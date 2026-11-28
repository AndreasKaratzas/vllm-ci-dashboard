import { effectScope } from 'vue'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

beforeEach(() => {
  vi.useFakeTimers()
  vi.setSystemTime(new Date('2026-10-19T10:00:00Z'))
  vi.resetModules()
})

afterEach(() => {
  vi.useRealTimers()
})

describe('useClock', () => {
  it('ticks every 30 s and is shared by every caller', async () => {
    const { useClock } = await import('@/composables/useClock')
    const scope = effectScope()
    const [first, second] = scope.run(() => [useClock(), useClock()])!
    expect(second).toBe(first)
    const start = first!.value
    vi.advanceTimersByTime(29_999)
    expect(first!.value).toBe(start)
    vi.advanceTimersByTime(1)
    expect(first!.value).toBe(start + 30_000)
    scope.stop()
    vi.advanceTimersByTime(60_000)
    expect(first!.value).toBe(start + 30_000)
  })
})
