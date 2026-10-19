import { createDnsLoader } from '@/domain/dns'
import type { Rec } from '@/domain/format'
import { fetchJSON, type FetchOptions } from './client'

const CI = 'data/vllm/ci/'
const RAW = 'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/'
// Collectors push these branches between Pages deployments, so they can be newer than Pages.
const QUEUE_LIVE = `${RAW}queue-data/${CI}`
const DNS_LIVE = `${RAW}dns-health-data/${CI}`

export const OPERATIONS_MANIFEST_PATH = `${CI}operations_v2_manifest.json`

/**
 * When a loaded dataset is refetched by the visible-page poll:
 * - sources: one of these manifest `shell.sources` timestamps changed;
 * - publication: the manifest generation changed;
 * - poll: every poll, because a live candidate advances independently of the manifest;
 * - manual: only on an explicit refresh.
 */
export type RefreshPolicy =
  | { on: 'sources'; sources: readonly string[] }
  | { on: 'publication' }
  | { on: 'poll' }
  | { on: 'manual' }

/** ISO timestamp a payload was published at; arbitrates candidates (newest wins) and labels updatedAt. */
export type PublishedAt = (payload: Rec) => string | undefined

/** Resolves the dataset; `fresh` bypasses caches, `upgrade` delivers a later, newer payload. */
export type DatasetLoad = (fresh: boolean, upgrade: (payload: Rec) => void) => Promise<Rec>

export interface DatasetSource {
  /** Candidates in priority order; ties on publishedAt go to the earlier one. */
  paths: readonly string[]
  refresh: RefreshPolicy
  publishedAt?: PublishedAt
  /** Rejects an incomplete payload so the fetch is retried. */
  validate?: (payload: unknown) => void
  /** Replaces the default loader (fetch every candidate, newest wins). */
  createLoader?: (paths: readonly string[]) => DatasetLoad
}

const generatedAt: PublishedAt = (payload) => payload.generated_at

export function isPlainObject(value: unknown): value is Rec {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

function assertManifest(payload: unknown) {
  if (!isPlainObject(payload) || !isPlainObject(payload.shell) || !isPlainObject(payload.sections)) {
    throw new Error('Operations manifest is incomplete')
  }
}

function dnsLoader(paths: readonly string[]): DatasetLoad {
  let upgrade: (payload: Rec) => void = () => {}
  // The DNS loader paints the first valid candidate, then upgrades if a slower one is newer.
  const loader = createDnsLoader((source) => fetchJSON(source, { uncached: true }), (payload) => upgrade(payload), [...paths])
  return (fresh, onUpgrade) => {
    upgrade = onUpgrade
    if (fresh) loader.invalidate()
    return loader.load()
  }
}

export const DATASETS = {
  publication_status: {
    paths: [`${CI}publication_status.json`],
    refresh: { on: 'poll' },
    publishedAt: generatedAt,
  },
  operations_manifest: {
    paths: [OPERATIONS_MANIFEST_PATH],
    refresh: { on: 'poll' },
    publishedAt: generatedAt,
    validate: assertManifest,
  },
  prs: {
    paths: ['data/vllm/prs.json'],
    refresh: { on: 'publication' },
    publishedAt: (payload) => payload.collected_at,
  },
  issues: {
    paths: ['data/vllm/issues.json'],
    refresh: { on: 'publication' },
    publishedAt: (payload) => payload.collected_at,
  },
  perf_eval: {
    paths: ['data/vllm/perf_eval/perf_eval.json'],
    refresh: { on: 'publication' },
    publishedAt: generatedAt,
  },
  amd_test_matrix: {
    paths: [`${CI}amd_test_matrix.json`],
    refresh: { on: 'sources', sources: ['amd_test_matrix'] },
    publishedAt: generatedAt,
  },
  workload_mapping: {
    paths: [`${CI}workload_mapping.json`],
    refresh: { on: 'sources', sources: ['workload_mapping'] },
    publishedAt: generatedAt,
  },
  dns: {
    paths: [`${DNS_LIVE}dns_failures.json`, `${CI}dns_failures.json`],
    refresh: { on: 'poll' },
    publishedAt: generatedAt,
    createLoader: dnsLoader,
  },
} as const satisfies Record<string, DatasetSource>

export type DatasetKey = keyof typeof DATASETS

export interface SectionSource {
  /** Published path; the manifest's descriptor path is authoritative when loading. */
  path: string
  refresh: RefreshPolicy
  /** A live candidate that competes with the published section (newest publishedAt wins). */
  live?: string
  publishedAt?: PublishedAt
  timeoutMs?: number
}

const section = (name: string, refresh: RefreshPolicy, extra: Partial<SectionSource> = {}): SectionSource => ({
  path: `${CI}operations_v2/${name}.json`,
  refresh,
  ...extra,
})

// Section inputs mirror build_operations_snapshot.py: a section changes only when one of its
// collector sources does, so an unchanged source set means the published section is unchanged.
export const SECTIONS = {
  nightly: section('nightly', { on: 'sources', sources: ['analytics', 'ci_health'] }),
  amd_test_health: section('amd_test_health', {
    on: 'sources',
    sources: ['analytics', 'ci_health', 'config_parity', 'amd_test_matrix'],
  }),
  amd_agent_health: section('amd_agent_health', { on: 'sources', sources: ['agent_health'] }),
  reliability: section('reliability', { on: 'manual' }, { timeoutMs: 120_000 }),
  comparison: section('comparison', { on: 'sources', sources: ['analytics'] }),
  definition_parity: section('definition_parity', { on: 'sources', sources: ['config_parity'] }),
  test_group_parity: section('test_group_parity', { on: 'sources', sources: ['test_group_parity', 'config_parity'] }),
  ownership: section('ownership', { on: 'sources', sources: ['ci_ownership'] }),
  queue: section(
    'queue',
    { on: 'poll' },
    { live: `${QUEUE_LIVE}operations_v2/queue.json`, publishedAt: (payload) => payload.queue?.snapshot?.ts },
  ),
  omni: section('omni', {
    on: 'sources',
    sources: ['queue_timeseries', 'queue_jobs', 'omni_heuristic', 'omni_issue_state', 'workload_mapping', 'capacity_monitor'],
  }),
  diagnostics: section('diagnostics', { on: 'publication' }),
} as const satisfies Record<string, SectionSource>

/** Published files that are linked to but never loaded by the page. */
export const LINKS = {
  queueHistory: `${QUEUE_LIVE}queue_timeseries.jsonl`,
} as const

function publishedMs(payload: Rec, publishedAt: PublishedAt | undefined): number {
  const parsed = Date.parse(String(publishedAt?.(payload) ?? ''))
  return Number.isFinite(parsed) ? parsed : -Infinity
}

/** The newest payload by publishedAt; ties keep candidate order. */
export function newest(payloads: Rec[], publishedAt: PublishedAt | undefined): Rec | undefined {
  let best: Rec | undefined
  let bestMs = -Infinity
  for (const payload of payloads) {
    const ms = publishedMs(payload, publishedAt)
    if (best === undefined || ms > bestMs) {
      best = payload
      bestMs = ms
    }
  }
  return best
}

/** Fetch every candidate and keep the newest that loaded; fails only when none did. */
export async function loadNewest(
  paths: readonly string[],
  publishedAt: PublishedAt | undefined,
  options: FetchOptions = {},
): Promise<Rec> {
  if (paths.length === 1) return fetchJSON<Rec>(paths[0]!, options)
  const results = await Promise.allSettled(paths.map((path) => fetchJSON<Rec>(path, options)))
  const loaded = results.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : []))
  const best = newest(loaded, publishedAt)
  if (best === undefined) {
    const reasons = results.flatMap((result) => (result.status === 'rejected' ? [String(result.reason?.message ?? result.reason)] : []))
    throw new Error(`No candidate is available (${reasons.join('; ')})`)
  }
  return best
}

/** The loader useDataset runs for a registered dataset. */
export function datasetLoader(key: DatasetKey): DatasetLoad {
  const source: DatasetSource = DATASETS[key]
  if (source.createLoader) return source.createLoader(source.paths)
  return (fresh) => loadNewest(source.paths, source.publishedAt, { uncached: fresh, validate: source.validate })
}
