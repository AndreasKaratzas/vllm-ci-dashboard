import type { Detail } from '@/components/evidence/detail'
import { link, text, type ColumnSpec } from '@/components/table/columns'
import { LATENCY_ASSET, latencyEvidence, latencyLabel, type LatencyComparison, type LatencyEvidenceRow } from '@/domain/analytics'
import type { Rec } from '@/domain/format'

/** Group labels are published lowercase ("model executor"); titles read as sentences. */
export function latencyTitle(row: Rec): string {
  const label = latencyLabel(row)
  return label ? label.charAt(0).toUpperCase() + label.slice(1) : 'AMD MI nightly latency'
}

const COLUMNS: ColumnSpec<LatencyEvidenceRow>[] = [
  { id: 'nightly', label: 'Main CI nightly', value: (row) => Number(row.number), cell: (row) => link(`#${row.number}`, row.buildUrl, true) },
  { id: 'created', label: 'Nightly created', value: (row) => row.created, cell: (row) => text(row.created) },
  {
    id: 'wall',
    label: 'Group wall completion',
    numeric: true,
    value: (row) => (row.observed ? row.wall : ''),
    cell: (row) => ({ text: row.wall, muted: !row.observed }),
  },
  {
    id: 'jobs',
    label: 'Exact MI jobs',
    sortable: false,
    value: (row) => row.jobs.map((job) => job.label).join(' '),
    cell: (row) => ({
      text: row.observed ? 'Exact job links unavailable' : 'No observation in this nightly',
      muted: true,
      links: row.jobs,
    }),
  },
]

/** One main CI test group's recent AMD MI wall completion, with every cohort nightly and its exact jobs. */
export function latencyDetail(row: Rec, comparison: LatencyComparison): Detail {
  const evidence = latencyEvidence(row, comparison)
  return {
    id: `latency:${row.id ?? latencyLabel(row)}`,
    title: latencyTitle(row),
    subtitle: 'Recent main CI AMD MI wall completion with exact nightly evidence',
    stats: [{ label: 'AMD MI median wall time', value: evidence.value, meta: evidence.meta }],
    sources: [{ label: 'Open latency data', url: LATENCY_ASSET }],
    tables: [
      {
        title: 'AMD MI observations',
        meta: 'The latest five main CI nightlies; no older backfill',
        columns: COLUMNS,
        rows: evidence.rows,
        pageSize: false,
      },
    ],
    footnote:
      'Each nightly contributes one observation: the longest wall completion time across the test group’s parallel shards. The metric is the median across the observed nightlies; missing observations stay unavailable.',
    width: 'lg',
  }
}
