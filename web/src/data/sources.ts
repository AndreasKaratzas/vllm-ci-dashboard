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
 * - interval: on a poll at least `minutes` after the last fetch, for files no manifest source tracks;
 * - manual: only on an explicit refresh.
 */
export type RefreshPolicy =
  | { on: 'sources'; sources: readonly string[] }
  | { on: 'publication' }
  | { on: 'poll' }
  | { on: 'interval'; minutes: number }
  | { on: 'manual' }

/** ISO timestamp a payload was published at; arbitrates candidates (newest wins) and labels updatedAt. */
export type PublishedAt = (payload: Rec) => string | undefined

/** Resolves the dataset; `fresh` bypasses caches, `upgrade` delivers a later, newer payload. */
export type DatasetLoad = (fresh: boolean, upgrade: (payload: Rec) => void) => Promise<Rec>

export interface DatasetSource {
  /** Candidates in priority order, the published fallback last; ties on publishedAt go to the earlier one. */
  paths: readonly string[]
  refresh: RefreshPolicy
  publishedAt?: PublishedAt
  /** Rejects an incomplete payload so the fetch is retried. */
  validate?: (payload: unknown) => void
  /** Replaces the default loader (fetch every candidate, newest wins). */
  createLoader?: (paths: readonly string[]) => DatasetLoad
  /** The publication surface that produces the dataset; while it is published as unavailable the dataset is not shown. */
  surface?: string
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

// hourly-master rewrites these every two hours; the manifest records none of them.
const COLLECTOR_RUN: RefreshPolicy = { on: 'interval', minutes: 60 }

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
    refresh: COLLECTOR_RUN,
    surface: 'github_home',
    publishedAt: (payload) => payload.collected_at,
  },
  issues: {
    paths: ['data/vllm/issues.json'],
    refresh: COLLECTOR_RUN,
    surface: 'github_home',
    publishedAt: (payload) => payload.collected_at,
  },
  perf_eval: {
    paths: ['data/vllm/perf_eval/perf_eval.json'],
    refresh: COLLECTOR_RUN,
    surface: 'perf_eval',
    publishedAt: generatedAt,
  },
  amd_test_matrix: {
    paths: [`${CI}amd_test_matrix.json`],
    refresh: { on: 'sources', sources: ['amd_test_matrix'] },
    surface: 'ci_core',
    publishedAt: generatedAt,
  },
  workload_mapping: {
    paths: [`${CI}workload_mapping.json`],
    refresh: { on: 'sources', sources: ['workload_mapping'] },
    surface: 'queue_workload',
    publishedAt: generatedAt,
  },
  dns: {
    paths: [`${DNS_LIVE}dns_failures.json`, `${CI}dns_failures.json`],
    refresh: { on: 'poll' },
    publishedAt: generatedAt,
    createLoader: dnsLoader,
    surface: 'dns_health',
  },
} as const satisfies Record<string, DatasetSource>

export type DatasetKey = keyof typeof DATASETS

export interface SectionSource {
  /** Published path that views cite as the source; loading follows the manifest descriptor. */
  path: string
  refresh: RefreshPolicy
  /** Manifest sources the section derives from, when its refresh policy does not name them. */
  inputs?: readonly string[]
  /** A live candidate that competes with the published section (newest publishedAt wins). */
  live?: string
  publishedAt?: PublishedAt
}

const inputs = (...keys: string[]): RefreshPolicy => ({ on: 'sources', sources: keys })
const section = (name: string, refresh: RefreshPolicy): SectionSource => ({ path: `${CI}operations_v2/${name}.json`, refresh })

// Section inputs mirror build_operations_snapshot.py: a section changes only when one of its
// collector sources does, so an unchanged source set means the published section is unchanged.
export const SECTIONS = {
  nightly: section('nightly', inputs('analytics', 'ci_health')),
  // amd_test_signal tracks the AMD test-result ledger, which no published source covers.
  amd_test_health: section('amd_test_health', inputs('analytics', 'ci_health', 'config_parity', 'amd_test_matrix', 'amd_test_signal')),
  amd_agent_health: section('amd_agent_health', inputs('agent_health')),
  reliability: { ...section('reliability', { on: 'manual' }), inputs: ['analytics'] },
  comparison: section('comparison', inputs('analytics')),
  definition_parity: section('definition_parity', inputs('config_parity')),
  test_group_parity: section('test_group_parity', inputs('test_group_parity', 'config_parity')),
  ownership: section('ownership', inputs('ci_ownership')),
  queue: {
    ...section('queue', { on: 'poll' }),
    inputs: ['queue_timeseries', 'queue_jobs'],
    live: `${QUEUE_LIVE}operations_v2/queue.json`,
    publishedAt: (payload) => payload.queue?.snapshot?.ts,
  },
  omni: section(
    'omni',
    inputs('queue_timeseries', 'queue_jobs', 'omni_heuristic', 'omni_issue_state', 'workload_mapping', 'capacity_monitor'),
  ),
  diagnostics: section('diagnostics', { on: 'publication' }),
  // The all-main cohort clock (main_ci) and the retry summary in analytics feed this section.
  main_ci: section('main_ci', inputs('analytics', 'main_ci')),
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

/** The newest payload the candidate requests resolved to; when none did, the published fallback's (last) error. */
export async function newestOf(requests: readonly Promise<Rec>[], publishedAt: PublishedAt | undefined): Promise<Rec> {
  const results = await Promise.allSettled(requests)
  const best = newest(
    results.flatMap((result) => (result.status === 'fulfilled' ? [result.value] : [])),
    publishedAt,
  )
  if (best === undefined) throw (results.at(-1) as PromiseRejectedResult).reason
  return best
}

/** Fetch every candidate path and keep the newest that loaded. */
export function loadNewest(paths: readonly string[], publishedAt: PublishedAt | undefined, options: FetchOptions = {}): Promise<Rec> {
  return newestOf(
    paths.map((path) => fetchJSON<Rec>(path, options)),
    publishedAt,
  )
}

/** The loader useDataset runs for a registered dataset. */
export function datasetLoader(key: DatasetKey): DatasetLoad {
  const source: DatasetSource = DATASETS[key]
  if (source.createLoader) return source.createLoader(source.paths)
  return (fresh) => loadNewest(source.paths, source.publishedAt, { uncached: fresh, validate: source.validate })
}
