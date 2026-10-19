import { getCurrentScope, onScopeDispose, ref, shallowRef, type Ref, type ShallowRef } from 'vue'
import { clearDataCache } from '@/data/client'
import {
  combineOperations,
  fetchSection,
  GenerationMismatchError,
  isManualSection,
  loadSections,
  policyStamp,
  samePublication,
  sectionStamp,
  type OperationsManifest,
} from '@/data/operations'
import { DATASETS, datasetLoader, OPERATIONS_MANIFEST_PATH, type DatasetKey, type DatasetSource } from '@/data/sources'
import type { Rec } from '@/domain/format'

const REFRESH_INTERVAL_MS = 5 * 60 * 1000

export interface Dataset<T = Rec> {
  data: ShallowRef<T | null>
  /** The last load or refresh failure; `data` keeps the last good payload. */
  error: Ref<string | null>
  /** A first load is in flight (no data yet). */
  loading: Ref<boolean>
  /** A refresh is in flight while data is shown. */
  refreshing: Ref<boolean>
  /** When the shown data was published (ISO), if known. */
  updatedAt: Ref<string | null>
  /** Refetch everything this dataset depends on, bypassing caches. */
  refresh: () => Promise<void>
}

export type DatasetSpec = DatasetKey | { sections: readonly string[] }

interface Entry {
  state: Dataset
  subscribers: number
  pending: Promise<void> | null
  load(fresh: boolean): Promise<void>
  /** Bring the data up to `manifest`; `isPoll` also refetches poll-policy data. */
  sync(manifest: OperationsManifest | null, isPoll: boolean): Promise<void>
}

const entries = new Map<string, Entry>()
const active = new Set<Entry>()
let subscriptions = 0
let timer: ReturnType<typeof setInterval> | null = null
let lastPollAt = 0
let polling: Promise<void> | null = null

function message(error: unknown) {
  return error instanceof Error ? error.message : String(error)
}

function createState(refresh: () => Promise<void>): Dataset {
  return { data: shallowRef(null), error: ref(null), loading: ref(false), refreshing: ref(false), updatedAt: ref(null), refresh }
}

/** Runs one load at a time per entry; `after` queues behind a pending load instead of joining it. */
function run(entry: Entry, task: () => Promise<void>, after = false): Promise<void> {
  if (entry.pending) return after ? entry.pending.then(() => run(entry, task)) : entry.pending
  const { state } = entry
  const flag = state.data.value === null ? state.loading : state.refreshing
  flag.value = true
  entry.pending = task()
    .then(
      () => {
        state.error.value = null
      },
      (error) => {
        state.error.value = message(error)
      },
    )
    .finally(() => {
      flag.value = false
      entry.pending = null
    })
  return entry.pending
}

function currentManifest(): OperationsManifest | null {
  return (entries.get('operations_manifest')?.state.data.value as OperationsManifest | null | undefined) ?? null
}

function createAssetEntry(key: DatasetKey): Entry {
  const source: DatasetSource = DATASETS[key]
  const load = datasetLoader(key)
  // undefined: loaded before any manifest was known, so the first manifest seen is adopted.
  let stamp: string | null | undefined
  let fetchedAt = 0
  const state = createState(() => entry.load(true))
  const show = (payload: Rec) => {
    state.data.value = payload
    state.updatedAt.value = source.publishedAt?.(payload) ?? null
  }
  const entry: Entry = {
    state,
    subscribers: 0,
    pending: null,
    load: (fresh) =>
      run(
        entry,
        async () => {
          const manifest = currentManifest()
          show(await load(fresh, show))
          fetchedAt = Date.now()
          stamp = manifest ? policyStamp(source.refresh, manifest) : undefined
        },
        fresh,
      ),
    async sync(manifest, isPoll) {
      const { refresh } = source
      if (refresh.on === 'poll' || refresh.on === 'interval') {
        const due = refresh.on === 'poll' || Date.now() - fetchedAt >= refresh.minutes * 60_000
        if (isPoll && due) await entry.load(true)
        return
      }
      if (!manifest || state.data.value === null) return
      const next = policyStamp(source.refresh, manifest)
      if (next === '' || next === stamp) return
      if (stamp === undefined) {
        stamp = next
        return
      }
      await entry.load(true)
    },
  }
  return entry
}

async function manifestSource(fresh: boolean): Promise<OperationsManifest> {
  const manifest = entryFor('operations_manifest')
  if (fresh || manifest.state.data.value === null) await manifest.load(fresh)
  const data = manifest.state.data.value as OperationsManifest | null
  if (data === null || (fresh && manifest.state.error.value)) {
    throw new Error(manifest.state.error.value ?? 'Operations manifest is unavailable')
  }
  return data
}

function createOperationsEntry(names: readonly string[]): Entry {
  const state = createState(() => entry.load(true))
  // Data built from a manual section (reliability) is only ever refreshed explicitly.
  const automatic = !names.some(isManualSection)
  let shown: OperationsManifest | null = null
  let payloads: Record<string, Rec> = {}
  let stamps: Record<string, string | null> = {}
  const show = (manifest: OperationsManifest, sections: Record<string, Rec>) => {
    shown = manifest
    payloads = sections
    stamps = Object.fromEntries(names.map((name) => [name, sectionStamp(manifest, name)]))
    state.data.value = combineOperations(manifest, names.map((name) => sections[name]!))
    state.updatedAt.value = manifest.generated_at ?? null
  }
  const reload = async (fresh: boolean) => {
    const { manifest, sections } = await loadSections(names, manifestSource, fresh)
    show(manifest, sections)
  }
  const entry: Entry = {
    state,
    subscribers: 0,
    pending: null,
    load: (fresh) => run(entry, () => reload(fresh), fresh),
    async sync(manifest, isPoll) {
      const previous = shown
      if (!automatic || !manifest || !previous) return
      const changed = names.filter((name) => {
        const stamp = sectionStamp(manifest, name)
        return stamp === null ? isPoll : stamp !== stamps[name]
      })
      if (!changed.length && manifest.generated_at === previous.generated_at) return
      await run(entry, async () => {
        let fetched: Rec[]
        try {
          fetched = await Promise.all(changed.map((name) => fetchSection(manifest, name, true)))
        } catch (error) {
          if (!(error instanceof GenerationMismatchError)) throw error
          return reload(true)
        }
        const unchanged =
          manifest.generated_at === previous.generated_at &&
          changed.every((name, index) => samePublication(name, payloads[name], fetched[index]!))
        if (unchanged) return
        const sections = { ...payloads }
        changed.forEach((name, index) => (sections[name] = fetched[index]!))
        show(manifest, sections)
      })
    },
  }
  return entry
}

function cacheKeyOf(spec: DatasetSpec): string {
  return typeof spec === 'string' ? spec : `sections:${[...new Set(spec.sections)].sort().join(',')}`
}

function entryFor(spec: DatasetSpec): Entry {
  const key = cacheKeyOf(spec)
  let entry = entries.get(key)
  if (!entry) {
    entry =
      typeof spec === 'string' ? createAssetEntry(spec) : createOperationsEntry([...new Set(spec.sections)].sort())
    entries.set(key, entry)
  }
  return entry
}

/** Poll the manifest, then refetch only what changed in each dataset a mounted view uses. */
function poll(): Promise<void> {
  polling ??= (async () => {
    lastPollAt = Date.now()
    const manifestEntry = entryFor('operations_manifest')
    const before = currentManifest()?.generated_at
    await manifestEntry.load(true)
    const manifest = manifestEntry.state.error.value ? null : currentManifest()
    if (manifest && manifest.generated_at !== before) clearDataCache(OPERATIONS_MANIFEST_PATH)
    await Promise.all([...active].filter((entry) => entry !== manifestEntry).map((entry) => entry.sync(manifest, true)))
  })().finally(() => {
    polling = null
  })
  return polling
}

function onVisibilityChange() {
  if (document.visibilityState === 'visible' && Date.now() - lastPollAt >= REFRESH_INTERVAL_MS) void poll()
}

function startPolling() {
  lastPollAt = Date.now()
  timer = setInterval(() => {
    if (document.visibilityState !== 'hidden') void poll()
  }, REFRESH_INTERVAL_MS)
  document.addEventListener('visibilitychange', onVisibilityChange)
}

function stopPolling() {
  if (timer) clearInterval(timer)
  timer = null
  document.removeEventListener('visibilitychange', onVisibilityChange)
}

function subscribe(entry: Entry) {
  if (!getCurrentScope()) return
  entry.subscribers += 1
  active.add(entry)
  subscriptions += 1
  if (subscriptions === 1) startPolling()
  onScopeDispose(() => {
    entry.subscribers -= 1
    if (!entry.subscribers) active.delete(entry)
    subscriptions -= 1
    if (!subscriptions) stopPolling()
  })
}

/**
 * A published dataset or a set of operations sections, shared by every caller with the same
 * spec. While any caller's scope is alive and the page is visible, the publication is polled
 * every five minutes and the data is refetched only when its inputs changed.
 */
export function useDataset<T = Rec>(spec: DatasetSpec): Dataset<T> {
  const entry = entryFor(spec)
  const resumed = entry.subscribers === 0 && entry.state.data.value !== null
  subscribe(entry)
  if (entry.state.data.value === null && !entry.pending) void entry.load(false)
  else if (resumed) void entry.sync(currentManifest(), false)
  return entry.state as Dataset<T>
}
