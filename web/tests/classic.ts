// Loads the classic renderer (docs/assets/js/ops-v2.js) and exposes its
// internal functions so v2 ports can be compared against them.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect } from 'vitest'
import { canonical, goldenArea, normalize } from './support/golden'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ClassicFns = Record<string, (...args: any[]) => any>

export function loadClassic(names: string[], file = 'ops-v2.js'): ClassicFns {
  const source = readFileSync(resolve(process.cwd(), '../docs/assets/js', file), 'utf8')
  const end = source.lastIndexOf('})();')
  if (end < 0) throw new Error('classic renderer IIFE not found')
  const key = `__classic_${names.length}_${Math.random().toString(36).slice(2)}`
  const exposed = `${source.slice(0, end)}window.${key} = {${names.join(',')}};\n${source.slice(end)}`
  // The classic IIFE only wires listeners at load; keep it off the network.
  globalThis.fetch = (() => new Promise(() => {})) as typeof fetch
  new Function(exposed)()
  return (window as unknown as Record<string, ClassicFns>)[key]!
}

const TONE_BY_CLASS: Record<string, string> = {
  'is-success': 'pass',
  'is-danger': 'hard',
  'is-warning': 'soft',
  'is-info': 'info',
  'is-neutral': 'neutral',
  'is-not-targeted': 'not-targeted',
}
const TONE_CLASS = /(?<![\w-])is-(?:success|danger|warning|info|neutral|not-targeted)(?![\w-])/g

/** Normalized classic data with its CSS-class tone names spelled as the v2 Tone. */
function fromClassic(value: unknown): unknown {
  if (typeof value === 'string') return value.replace(TONE_CLASS, (t) => TONE_BY_CLASS[t]!)
  if (Array.isArray(value)) return value.map(fromClassic)
  if (value === null || typeof value !== 'object') return value
  return Object.fromEntries(Object.entries(value).map(([key, item]) => [key, fromClassic(item)]))
}

/**
 * Asserts that the v2 value equals the classic one once both are canonicalized,
 * and folds the v2 value into the area's golden digest.
 */
export function classicParity(area: string) {
  const expectGolden = goldenArea(area)
  return (fn: string, port: unknown, classic: unknown, message?: string): void => {
    const expected = fromClassic(normalize(classic))
    if (canonical(port) !== canonical(expected)) {
      expect(normalize(port), message ?? fn).toEqual(expected)
      expect(canonical(port), message ?? fn).toBe(canonical(expected))
    }
    expectGolden(fn, port)
  }
}
