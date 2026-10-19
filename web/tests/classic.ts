// Loads the classic renderer (docs/assets/js/ops-v2.js) and exposes its
// internal functions so v2 ports can be compared against them.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { expect } from 'vitest'
import { canonical, expectGolden, normalize } from './support/golden'

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

/**
 * Asserts that the v2 value equals the classic one once both are canonicalized,
 * and folds the v2 value into the area's golden digest.
 */
export function classicParity(area: string) {
  return (fn: string, port: unknown, classic: unknown, message?: string): void => {
    if (canonical(port) !== canonical(classic)) {
      expect(normalize(port), message ?? fn).toEqual(normalize(classic))
      expect(canonical(port), message ?? fn).toBe(canonical(classic))
    }
    expectGolden(area, fn, port)
  }
}
