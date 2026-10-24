import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterEach, beforeEach, vi } from 'vitest'

const FIXTURES = resolve(import.meta.dirname, '../fixtures')

export function readFixture<T = unknown>(name: string): T {
  return JSON.parse(readFileSync(resolve(FIXTURES, name), 'utf8')) as T
}

/**
 * Pins Date (only Date; timers stay real) to `iso` around every test of the
 * calling file and returns it in ms. Goldens must not depend on when they run.
 */
export function pinClock(iso: string): number {
  const now = Date.parse(iso)
  if (!Number.isFinite(now)) throw new Error(`pinClock: invalid timestamp ${iso}`)
  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] })
    vi.setSystemTime(now)
  })
  afterEach(() => {
    vi.useRealTimers()
  })
  return now
}
