import { shortDayTime } from '@/components/charts/chart'
import type { Detail, DetailField } from '@/components/evidence/detail'
import { hardwareCell, link, type ColumnSpec } from '@/components/table/columns'
import { age, integer } from '@/domain/format'
import { groupLabel } from '@/domain/mainCi'
import {
  causeLabel,
  causeShort,
  changeLabel,
  increaseLabel,
  levelsLabel,
  levelsText,
  minutes,
  slowdownSearchText,
  slowdownTone,
  stepLabel,
  type BuildRef,
  type Slowdown,
  type Slowdowns,
} from '@/domain/slowdowns'
import SlowdownChart from './SlowdownChart.vue'

export const slowdownName = (row: Slowdown) => groupLabel(stepLabel(row))

/** Active and settled tables share these; `compact` keeps the step, its increase and its onset for side panels. */
export function slowdownColumns(open: (row: Slowdown) => void, nowMs: number, { compact = false } = {}): ColumnSpec<Slowdown>[] {
  const columns: ColumnSpec<Slowdown>[] = [
    {
      id: 'step',
      label: 'Test step',
      hideable: false,
      value: (row) => slowdownName(row),
      cell: (row) => ({ text: slowdownName(row), onOpen: () => open(row), sub: row.queue || undefined }),
    },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden md:table-cell' },
    { id: 'levels', label: 'Before → now', value: (row) => row.recentMins, cell: (row) => ({ text: levelsText(row), nowrap: true }), class: 'hidden lg:table-cell' },
    {
      id: 'increase',
      label: 'Increase',
      numeric: true,
      value: (row) => row.increaseMins,
      cell: (row) => ({ text: increaseLabel(row), tone: slowdownTone(row), nowrap: true }),
    },
    {
      id: 'since',
      label: 'Since',
      value: (row) => row.onset.createdAt,
      cell: (row) => ({ ...link(changeLabel(row), row.onset.url, true), sub: age(row.onset.createdAt, nowMs) }),
      class: 'hidden whitespace-nowrap sm:table-cell',
    },
    {
      id: 'cause',
      label: 'Change',
      value: (row) => causeShort(row),
      cell: (row) => link(causeShort(row), row.compareUrl || null),
      class: 'hidden xl:table-cell',
    },
  ]
  return compact ? columns.filter((column) => ['step', 'increase', 'since'].includes(column.id)) : columns
}

export const slowdownSearch = slowdownSearchText

const buildField = (label: string, ref: BuildRef | null): DetailField => ({
  label,
  value: ref ? `#${ref.number}${ref.createdAt ? ` · ${shortDayTime(Date.parse(ref.createdAt))} UTC` : ''}` : null,
  href: ref?.url || undefined,
})

/** One slowdown: the level before and since, every sampled build, the builds around the onset and the commit range. */
export function slowdownDetail(row: Slowdown, data: Pick<Slowdowns, 'issue' | 'rules'>, nowMs: number): Detail {
  const before = row.samples.filter((sample) => !sample.slow).length
  return {
    id: `slowdown:${row.id}`,
    title: slowdownName(row),
    subtitle: [row.hardware.toUpperCase(), row.queue, `slower since #${row.onset.number}, ${age(row.onset.createdAt, nowMs)}`].filter(Boolean).join(' · '),
    description: causeLabel(row) + '.',
    stats: [
      { label: 'Before', value: minutes(row.baselineMins), meta: `${integer(row.baselineRuns)} runs` },
      { label: 'Now', value: minutes(row.recentMins), meta: `${integer(row.recentRuns)} runs` },
      { label: 'Increase', value: increaseLabel(row), tone: slowdownTone(row), tint: true },
      { label: 'Run-to-run noise', value: row.noiseMins === null ? '—' : `±${minutes(row.noiseMins)}`, meta: 'Per-run sigma' },
    ],
    sections: [
      {
        title: 'Wall time per build',
        meta: `${integer(before)} sampled runs before, ${integer(row.samples.length - before)} since`,
        component: SlowdownChart,
        props: { row },
      },
    ],
    fields: [
      { label: 'Status', value: row.status === 'active' ? 'Active' : 'Settled', tone: slowdownTone(row) },
      { label: 'Levels', value: levelsLabel(row) },
      buildField('Last fast build', row.lastGood),
      buildField('First slow build', row.onset),
      buildField('Latest rise', row.lastRise && row.lastRise.number !== row.onset.number ? row.lastRise : null),
      buildField('Latest build', row.latest),
      { label: 'vLLM commits', value: row.compareUrl ? changeLabel(row) : null, href: row.compareUrl || undefined },
      { label: 'Shards per build', value: row.jobs > 1 ? integer(row.jobs) : null },
      { label: 'First detected', value: row.firstDetectedAt ? `${shortDayTime(Date.parse(row.firstDetectedAt))} UTC` : null },
    ],
    sources: [
      { label: 'First slow build', url: row.onset.url },
      { label: 'Compare vLLM commits', url: row.compareUrl },
      { label: `Issue #${data.issue?.number ?? ''}`, url: row.status === 'active' ? (data.issue?.url ?? '') : '' },
    ],
    footnote: data.rules
      ? `A slowdown settles ${integer(data.rules.settleAfterDays)} days after its latest rise and then leaves the issue; it resolves earlier if the step returns to its earlier level.`
      : undefined,
    width: 'lg',
  }
}
