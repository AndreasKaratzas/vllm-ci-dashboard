import type { Rec } from '@/domain/format'
import { fetchJSON } from './client'
import { DATASETS, isPlainObject, newestOf, OPERATIONS_MANIFEST_PATH, SECTIONS, type RefreshPolicy, type SectionSource } from './sources'

export interface SectionDescriptor {
  path: string
  bytes?: number
  /** When both this and the section payload's `generation` are present they must match. */
  generation?: string
  /** Hex SHA-256 of the published section file. */
  sha256?: string
}

export interface OperationsManifest extends Rec {
  generated_at?: string
  shell: Rec
  sections: Record<string, SectionDescriptor | undefined>
}

/** Fetches the manifest; `fresh` bypasses every cache. */
export type ManifestSource = (fresh: boolean) => Promise<OperationsManifest>

export class GenerationMismatchError extends Error {}

/** A section derived from a publication surface the selector published as unavailable. */
export class SectionUnavailableError extends Error {}

/** One `shell.publication.unavailable` row of the manifest. */
export interface UnavailableSurface {
  surface: string
  since?: string | null
  reason?: string | null
  codes?: string[]
  sources: string[]
}

const MANIFEST_DIR = OPERATIONS_MANIFEST_PATH.slice(0, OPERATIONS_MANIFEST_PATH.lastIndexOf('/') + 1)
const UNREGISTERED_SECTION: SectionSource = { path: '', refresh: { on: 'publication' } }

function mergeOperationPayload(target: Rec, source: Rec | null | undefined): Rec {
  for (const [key, value] of Object.entries(source || {})) {
    target[key] = isPlainObject(value)
      ? mergeOperationPayload(isPlainObject(target[key]) ? target[key] : {}, value)
      : value
  }
  return target
}

function sectionSource(name: string): SectionSource {
  return (SECTIONS as Record<string, SectionSource>)[name] ?? UNREGISTERED_SECTION
}

function sectionInputs(name: string): readonly string[] {
  const source = sectionSource(name)
  return source.inputs ?? (source.refresh.on === 'sources' ? source.refresh.sources : [])
}

/** Surfaces whose candidate and last-known-good data both failed validation. */
export function unavailableSurfaces(manifest: OperationsManifest): UnavailableSurface[] {
  const rows: unknown = manifest.shell.publication?.unavailable
  if (!Array.isArray(rows)) return []
  return rows.filter(
    (row): row is UnavailableSurface => isPlainObject(row) && typeof row.surface === 'string' && Array.isArray(row.sources),
  )
}

/** The unavailable surfaces a section derives from; such a section is shown as unavailable, never as data. */
export function sectionUnavailability(manifest: OperationsManifest, name: string): UnavailableSurface[] {
  const inputs = sectionInputs(name)
  return unavailableSurfaces(manifest).filter((row) => row.sources.some((source) => inputs.includes(source)))
}

function unavailableMessage(rows: UnavailableSurface[]): string {
  const surfaces = rows.map((row) => row.surface.replaceAll('_', ' ')).join(', ')
  const since = rows.map((row) => row.since).filter((value): value is string => typeof value === 'string' && !!value).sort()[0]
  const codes = [...new Set(rows.flatMap((row) => (Array.isArray(row.codes) ? row.codes : [])))].slice(0, 5)
  return [
    `Unavailable: the latest ${surfaces} data and its last-known-good snapshot both failed validation`,
    codes.length ? ` (${codes.join(', ')})` : '',
    since ? ` since ${since}` : '',
    '. It returns after a collection passes validation.',
  ].join('')
}

export function fetchManifest(fresh = false): Promise<OperationsManifest> {
  return fetchJSON<OperationsManifest>(OPERATIONS_MANIFEST_PATH, { uncached: fresh, validate: DATASETS.operations_manifest.validate })
}

async function sha256Hex(body: ArrayBuffer): Promise<string | null> {
  // SubtleCrypto exists only in secure contexts; elsewhere the generation field is the only check.
  const subtle = globalThis.crypto?.subtle
  if (!subtle) return null
  const digest = new Uint8Array(await subtle.digest('SHA-256', body))
  return Array.from(digest, (byte) => byte.toString(16).padStart(2, '0')).join('')
}

function sectionValidator(name: string, descriptor: SectionDescriptor) {
  return async (payload: unknown, body: ArrayBuffer) => {
    const generation = isPlainObject(payload) ? payload.generation : undefined
    if (descriptor.generation !== undefined && generation !== undefined && generation !== descriptor.generation) {
      throw new GenerationMismatchError(`Section "${name}" is generation ${generation}, the manifest expects ${descriptor.generation}`)
    }
    if (descriptor.sha256) {
      const digest = await sha256Hex(body)
      if (digest !== null && digest !== descriptor.sha256.toLowerCase()) {
        throw new GenerationMismatchError(`Section "${name}" does not match the manifest sha256`)
      }
    }
  }
}

/** One section of a manifest; a section with a live candidate resolves to the newer of the two. */
export async function fetchSection(manifest: OperationsManifest, name: string, fresh = false): Promise<Rec> {
  const descriptor = manifest.sections[name]
  if (!descriptor || !descriptor.path) throw new Error(`Operations section "${name}" is missing from the manifest`)
  const unavailable = sectionUnavailability(manifest, name)
  if (unavailable.length) throw new SectionUnavailableError(unavailableMessage(unavailable))
  const source = sectionSource(name)
  const path = MANIFEST_DIR + descriptor.path.replace(/^\/+/, '')
  const published = fetchJSON<Rec>(path, {
    uncached: fresh,
    // A manual section's stamp never changes, so it is downloaded once per page as first loaded.
    cacheVersion: sectionStamp(manifest, name) ?? String(manifest.generated_at ?? ''),
    validate: sectionValidator(name, descriptor),
  })
  if (!source.live) return published
  return newestOf([fetchJSON<Rec>(source.live, { uncached: fresh }), published], source.publishedAt)
}

async function fetchSections(manifest: OperationsManifest, names: readonly string[], fresh: boolean) {
  const payloads = await Promise.all(names.map((name) => fetchSection(manifest, name, fresh)))
  return Object.fromEntries(names.map((name, index) => [name, payloads[index]!])) as Record<string, Rec>
}

/** The manifest and its named sections, all from one generation. */
export async function loadSections(names: readonly string[], manifestSource: ManifestSource, fresh = false) {
  const manifest = await manifestSource(fresh)
  try {
    return { manifest, sections: await fetchSections(manifest, names, fresh) }
  } catch (error) {
    if (!(error instanceof GenerationMismatchError)) throw error
    // Sections newer than the manifest mean the manifest is the stale half of a deployment race.
    const current = await manifestSource(true)
    return { manifest: current, sections: await fetchSections(current, names, true) }
  }
}

/** The manifest shell merged with section payloads, later sections winning. */
export function combineOperations(manifest: OperationsManifest, sections: Iterable<Rec>): Rec {
  const combined = mergeOperationPayload({}, manifest.shell)
  for (const payload of sections) mergeOperationPayload(combined, payload)
  return combined
}

/** Merge the manifest shell with the named sections. */
export async function loadOperations(names: readonly string[]): Promise<Rec> {
  const { manifest, sections } = await loadSections(names, fetchManifest)
  return combineOperations(manifest, Object.values(sections))
}

/**
 * What a refresh policy depends on in a manifest: equal stamps mean nothing to refetch.
 * null means "always refetch" (poll) and '' means "never because of the manifest" (interval, manual).
 */
export function policyStamp(policy: RefreshPolicy, manifest: OperationsManifest): string | null {
  switch (policy.on) {
    case 'poll':
      return null
    case 'interval':
    case 'manual':
      return ''
    case 'publication':
      return String(manifest.generated_at ?? '')
    case 'sources': {
      const sources: Rec = manifest.shell.sources ?? {}
      return JSON.stringify(policy.sources.map((key) => sources[key]?.timestamp ?? null))
    }
  }
}

export function sectionStamp(manifest: OperationsManifest, name: string): string | null {
  const stamp = policyStamp(sectionSource(name).refresh, manifest)
  if (!stamp) return stamp
  const descriptor = manifest.sections[name]
  return JSON.stringify([stamp, descriptor?.generation ?? null, descriptor?.sha256 ?? null])
}

/** True when both payloads of a live-arbitrated section carry the same publication time. */
export function samePublication(name: string, left: Rec | undefined, right: Rec): boolean {
  const publishedAt = sectionSource(name).publishedAt
  const stamp = left && publishedAt?.(left)
  return !!stamp && stamp === publishedAt?.(right)
}

export function isManualSection(name: string): boolean {
  return sectionSource(name).refresh.on === 'manual'
}
