// Golden digests: every value a test passes to expectGolden is canonicalized
// and folded into a sha256 per (area, test, fn), cut into chunks of CHUNK calls
// so a mismatch names a narrow call range. `GOLDEN_RECORD=1 npx vitest run -u`
// records the tests that ran into tests/golden/<area>.json and rewrites the
// readable tests/golden/<area>/*.json snapshots; otherwise both are compared.
import { createHash, type Hash } from 'node:crypto'
import { existsSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { afterAll, expect, onTestFinished, type RunnerTask, type RunnerTestFile, type RunnerTestSuite } from 'vitest'

// shortDate() renders in local time; recorded digests assume UTC.
process.env.TZ = 'UTC'

const RECORD = process.env.GOLDEN_RECORD === '1'
const CHUNK = 100
const DIGEST_HEX = 16
const GOLDEN_DIR = resolve(import.meta.dirname, '../golden')

/**
 * The value as plain data, with the leniency of toEqual: object keys sorted,
 * undefined properties dropped, Map/Set as arrays, Dates as ISO strings.
 */
export function normalize(value: unknown, seen = new Set<object>()): unknown {
  if (value === null || typeof value !== 'object') {
    if (typeof value === 'symbol' || typeof value === 'bigint') throw new TypeError(`golden values must be data, got ${typeof value}`)
    return value
  }
  if (typeof Node !== 'undefined' && value instanceof Node) throw new TypeError('golden values must be data, got a DOM node')
  if (seen.has(value)) throw new TypeError('golden values must not be cyclic')
  seen.add(value)
  let out: unknown
  if (value instanceof Date) out = Number.isNaN(value.getTime()) ? 'Invalid Date' : value.toISOString()
  else if (Array.isArray(value)) out = Array.from(value, (item) => normalize(item, seen))
  else if (value instanceof Set) out = [...value].map((item) => normalize(item, seen))
  else if (value instanceof Map) out = [...value].map((entry) => normalize(entry, seen))
  else {
    const obj: Record<string, unknown> = {}
    for (const key of Object.keys(value).sort()) {
      const item = (value as Record<string, unknown>)[key]
      if (item !== undefined) obj[key] = normalize(item, seen)
    }
    out = obj
  }
  seen.delete(value)
  return out
}

/** A deterministic encoding of normalize(value) that keeps undefined, NaN, ±Infinity, -0, and functions distinct. */
export function canonical(value: unknown): string {
  return encode(normalize(value))
}

function encode(v: unknown): string {
  if (v === undefined) return 'undefined'
  if (typeof v === 'number') return Object.is(v, -0) ? '-0' : String(v)
  // Prototype lookups (e.g. a label map indexed with 'constructor') return built-in functions.
  if (typeof v === 'function') return `function ${v.name}`
  if (v === null || typeof v !== 'object') return JSON.stringify(v)
  if (Array.isArray(v)) return `[${v.map(encode).join(',')}]`
  return `{${Object.entries(v)
    .map(([k, item]) => `${JSON.stringify(k)}:${encode(item)}`)
    .join(',')}}`
}

/** Matches a readable canonical rendering of `value` against tests/golden/<area>/<name>.json. */
export async function expectGoldenFile(area: string, name: string, value: unknown): Promise<void> {
  await expect(`${JSON.stringify(normalize(value), null, 2)}\n`).toMatchFileSnapshot(resolve(GOLDEN_DIR, area, `${name}.json`))
}

interface Digest {
  calls: number
  chunks: string[]
}
type AreaFile = Record<string, Record<string, Digest>>

interface Stream extends Digest {
  hash: Hash
  last: string
}

interface Area {
  stored: AreaFile
  recorded: AreaFile
  open: Map<string, Map<string, Stream>>
}

const areas = new Map<string, Area>()

const goldenPath = (area: string) => resolve(GOLDEN_DIR, `${area}.json`)

function where(area: string, test: string, fn: string) {
  return `golden ${area} › ${test} › ${fn}`
}

function closeChunk(area: string, test: string, fn: string, stream: Stream) {
  const index = stream.chunks.length
  const digest = stream.hash.digest('hex').slice(0, DIGEST_HEX)
  stream.chunks.push(digest)
  stream.hash = createHash('sha256')
  if (RECORD) return
  const expected = areas.get(area)!.stored[test]?.[fn]
  const first = index * CHUNK + 1
  expect(
    digest,
    `${where(area, test, fn)}: calls ${first}-${stream.calls} differ from the recording` +
      `${expected ? '' : ' (nothing recorded; run with GOLDEN_RECORD=1)'}; call ${stream.calls} was ${stream.last.slice(0, 400)}`,
  ).toBe(expected?.chunks[index])
}

function finishTest(area: string, test: string, failed: boolean) {
  const state = areas.get(area)!
  const streams = state.open.get(test)!
  state.open.delete(test)
  // A failed test stopped early: its partial digests are neither evidence nor a recording.
  if (failed) return
  const digests: Record<string, Digest> = {}
  for (const fn of [...streams.keys()].sort()) {
    const stream = streams.get(fn)!
    if (stream.calls % CHUNK) closeChunk(area, test, fn, stream)
    digests[fn] = { calls: stream.calls, chunks: stream.chunks }
  }
  if (state.recorded[test]) throw new Error(`${where(area, test, '*')}: test titles must be unique within an area`)
  state.recorded[test] = digests
  if (RECORD) return
  const expected = state.stored[test] ?? {}
  const calls = (d: Record<string, Digest>) => Object.fromEntries(Object.entries(d).map(([fn, { calls }]) => [fn, calls]))
  expect(calls(digests), `${where(area, test, '*')}: calls per fn differ from the recording`).toEqual(calls(expected))
}

function testsOf(suite: Readonly<RunnerTestSuite | RunnerTestFile>, out = new Map<string, RunnerTask>()) {
  for (const task of suite.tasks) {
    if (task.type === 'suite') testsOf(task, out)
    else out.set(task.fullTestName ?? task.name, task)
  }
  return out
}

/**
 * Runs once the area's file finishes. Recording rewrites the area from the
 * tests that passed and keeps the digests of tests that did not run or failed;
 * comparing fails on recorded tests that are gone or stopped calling expectGolden.
 */
function finishArea(area: string, file: Readonly<RunnerTestSuite | RunnerTestFile>) {
  const { stored, recorded } = areas.get(area)!
  const tests = testsOf(file)
  const passed = (name: string) => tests.get(name)?.result?.state === 'pass'
  if (RECORD) {
    const next: AreaFile = {}
    for (const name of tests.keys()) {
      const digests = passed(name) ? recorded[name] : stored[name]
      if (digests) next[name] = digests
    }
    writeArea(area, next)
    return
  }
  const problems = Object.keys(stored).flatMap((name) => {
    if (!tests.has(name)) return [`${where(area, name, '*')}: recorded test no longer exists`]
    if (passed(name) && !recorded[name]) return [`${where(area, name, '*')}: recorded test made no golden calls`]
    return []
  })
  if (problems.length) throw new Error(`${problems.join('\n')}\n(re-record with GOLDEN_RECORD=1 if intended)`)
}

function writeArea(area: string, file: AreaFile) {
  const tests = Object.keys(file).sort()
  const lines = ['{']
  tests.forEach((test, ti) => {
    lines.push(`  ${JSON.stringify(test)}: {`)
    const fns = Object.keys(file[test]!)
    fns.forEach((fn, fi) => {
      const { calls, chunks } = file[test]![fn]!
      const comma = fi < fns.length - 1 ? ',' : ''
      lines.push(`    ${JSON.stringify(fn)}: { "calls": ${calls}, "chunks": ${JSON.stringify(chunks)} }${comma}`)
    })
    lines.push(`  }${ti < tests.length - 1 ? ',' : ''}`)
  })
  lines.push('}', '')
  mkdirSync(GOLDEN_DIR, { recursive: true })
  writeFileSync(goldenPath(area), lines.join('\n'))
}

/**
 * Declares the golden area of the calling test file; call it once at module
 * top level. The returned expectGolden folds `value` into the digest of
 * (area, current test, fn).
 */
export function goldenArea(area: string): (fn: string, value: unknown) => void {
  if (areas.has(area)) throw new Error(`golden area ${area} is declared twice`)
  const path = goldenPath(area)
  const stored = existsSync(path) ? (JSON.parse(readFileSync(path, 'utf8')) as AreaFile) : {}
  areas.set(area, { stored, recorded: {}, open: new Map() })
  afterAll(({}, file) => finishArea(area, file))
  return (fn, value) => expectGolden(area, fn, value)
}

function expectGolden(area: string, fn: string, value: unknown): void {
  const test = expect.getState().currentTestName
  if (!test) throw new Error('expectGolden must run inside a test')
  const { open } = areas.get(area)!
  let streams = open.get(test)
  if (!streams) {
    streams = new Map()
    open.set(test, streams)
    onTestFinished(({ task }) => finishTest(area, test, task.result?.state === 'fail'))
  }
  let stream = streams.get(fn)
  if (!stream) {
    stream = { calls: 0, chunks: [], hash: createHash('sha256'), last: '' }
    streams.set(fn, stream)
  }
  stream.last = canonical(value)
  stream.hash.update(stream.last)
  stream.hash.update('\n')
  stream.calls += 1
  if (stream.calls % CHUNK === 0) closeChunk(area, test, fn, stream)
}
