// Loads the classic renderer (docs/assets/js/ops-v2.js) and exposes its
// internal functions so v2 ports can be compared against them.
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type ClassicFns = Record<string, (...args: any[]) => any>

export function loadClassic(names: string[]): ClassicFns {
  const source = readFileSync(resolve(process.cwd(), '../docs/assets/js/ops-v2.js'), 'utf8')
  const end = source.lastIndexOf('})();')
  if (end < 0) throw new Error('classic renderer IIFE not found')
  const key = `__classic_${names.length}_${Math.random().toString(36).slice(2)}`
  const exposed = `${source.slice(0, end)}window.${key} = {${names.join(',')}};\n${source.slice(end)}`
  // The classic IIFE only wires listeners at load; keep it off the network.
  globalThis.fetch = (() => new Promise(() => {})) as typeof fetch
  new Function(exposed)()
  return (window as unknown as Record<string, ClassicFns>)[key]!
}

export function readFixture<T = unknown>(name: string): T {
  return JSON.parse(readFileSync(resolve(process.cwd(), 'tests/fixtures', name), 'utf8')) as T
}
