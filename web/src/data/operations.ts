import type { Rec } from '@/domain/format'
import { fetchJSON } from './fetch'

export const OPERATIONS_MANIFEST = 'data/vllm/ci/operations_v2_manifest.json'
const QUEUE_LIVE_SECTION =
  'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-data/data/vllm/ci/operations_v2/queue.json'

function isPlainObject(value: unknown): value is Rec {
  return !!value && typeof value === 'object' && !Array.isArray(value)
}

export function mergeOperationPayload(target: Rec, source: Rec | null | undefined): Rec {
  for (const [key, value] of Object.entries(source || {})) {
    target[key] = isPlainObject(value)
      ? mergeOperationPayload(isPlainObject(target[key]) ? target[key] : {}, value)
      : value
  }
  return target
}

function sectionPath(relativePath: string) {
  return OPERATIONS_MANIFEST.slice(0, OPERATIONS_MANIFEST.lastIndexOf('/') + 1) + relativePath.replace(/^\/+/, '')
}

function queueTimestamp(section: Rec) {
  const parsed = new Date(section?.queue?.snapshot?.ts || '').getTime()
  return Number.isFinite(parsed) ? parsed : -Infinity
}

export async function operationsManifest(): Promise<Rec> {
  const manifest = await fetchJSON<Rec>(OPERATIONS_MANIFEST)
  if (!manifest || !manifest.shell || !manifest.sections) throw new Error('Operations manifest is incomplete')
  return manifest
}

/** Merge the manifest shell with the named sections, as the v1 renderer does. */
export async function loadOperations(sectionNames: string[]): Promise<Rec> {
  const manifest = await operationsManifest()
  const sections = await Promise.all(
    sectionNames.map((name) => {
      const descriptor = manifest.sections[name]
      if (!descriptor || !descriptor.path) throw new Error(`Operations section "${name}" is missing from the manifest`)
      const published = sectionPath(descriptor.path)
      if (name !== 'queue') return fetchJSON<Rec>(published)
      // The live queue branch refreshes every ten minutes; prefer whichever
      // candidate carries the newer snapshot.
      return Promise.allSettled([fetchJSON<Rec>(QUEUE_LIVE_SECTION), fetchJSON<Rec>(published)]).then((results) => {
        const candidates = results.flatMap((r) => (r.status === 'fulfilled' ? [r.value] : []))
        if (!candidates.length) throw new Error('No queue section is available')
        return candidates.sort((a, b) => queueTimestamp(b) - queueTimestamp(a))[0]!
      })
    }),
  )
  const combined = mergeOperationPayload({}, manifest.shell)
  for (const section of sections) mergeOperationPayload(combined, section)
  return combined
}
