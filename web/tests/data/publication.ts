import type { installServer } from './server'

export const MANIFEST = 'data/vllm/ci/operations_v2_manifest.json'
export const STATUS = 'data/vllm/ci/publication_status.json'
export const QUEUE_LIVE =
  'https://raw.githubusercontent.com/AndreasKaratzas/vllm-ci-dashboard/queue-data/data/vllm/ci/operations_v2/queue.json'
export const sectionPath = (name: string) => `data/vllm/ci/operations_v2/${name}.json`

const SECTION_NAMES = ['nightly', 'amd_test_health', 'reliability', 'test_group_parity', 'queue', 'omni']

export interface Publication {
  generatedAt: string
  /** shell.sources timestamps by source key. */
  sources: Record<string, string>
  /** Per-section descriptor extras (generation, sha256). */
  descriptors?: Record<string, Record<string, string>>
}

export function manifestOf({ generatedAt, sources, descriptors = {} }: Publication) {
  return {
    generated_at: generatedAt,
    shell: {
      generated_at: generatedAt,
      sources: Object.fromEntries(Object.entries(sources).map(([key, timestamp]) => [key, { path: `${key}.json`, timestamp }])),
      home: { status: 'healthy' },
    },
    sections: Object.fromEntries(
      SECTION_NAMES.map((name) => [name, { path: `operations_v2/${name}.json`, bytes: 1, ...descriptors[name] }]),
    ),
  }
}

/** Serves a manifest and one payload per section whose `label` names the publication it came from. */
export function publish(server: ReturnType<typeof installServer>, publication: Publication) {
  server.set(MANIFEST, manifestOf(publication))
  for (const name of SECTION_NAMES) {
    const generation = publication.descriptors?.[name]?.generation
    const body =
      name === 'queue'
        ? { queue: { snapshot: { ts: publication.generatedAt }, origin: 'pages' } }
        : { [name]: { label: `${name}@${publication.generatedAt}` }, ...(generation ? { generation } : {}) }
    server.set(sectionPath(name), body)
  }
}

export const SOURCES = {
  analytics: '2026-10-19T10:00:00Z',
  ci_health: '2026-10-19T10:00:00Z',
  config_parity: '2026-10-19T10:00:00Z',
  amd_test_matrix: '2026-10-19T10:00:00Z',
  test_group_parity: '2026-10-19T10:00:00Z',
  queue_timeseries: '2026-10-19T10:00:00Z',
  queue_jobs: '2026-10-19T10:00:00Z',
}
