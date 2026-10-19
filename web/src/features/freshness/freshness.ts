import type { Detail, DetailStat } from '@/components/evidence/detail'
import type { Freshness } from '@/components/layout/PageHeader.vue'
import { badge, mono, text, type ColumnSpec } from '@/components/table/columns'
import { age, shortDate } from '@/domain/format'
import {
  coreFreshness,
  queueFreshness,
  type FreshnessState,
  type FreshnessSummary,
  type SourceFreshness,
} from '@/domain/freshness'
import type { Tone } from '@/lib/tone'

export const FRESHNESS_TONE: Record<FreshnessState, Tone> = { fresh: 'pass', warn: 'soft', stale: 'hard' }
export const FRESHNESS_LABEL: Record<FreshnessState, string> = { fresh: 'Within target', warn: 'Past target', stale: 'Stale' }

const TARGETS_URL = 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/blob/main/config/data_freshness_targets.json'

/** "2h ago", or why there is no age. */
export function sourceAge(summary: Pick<FreshnessSummary, 'timestamp'> | null | undefined, nowMs: number): string {
  return summary?.timestamp ? age(summary.timestamp, nowMs) : 'unknown'
}

/** One line per source: "CI health 2h ago". */
export function sourceLines(sources: SourceFreshness[], nowMs: number): string[] {
  return sources.map((row) => `${row.label} ${row.timestamp ? age(row.timestamp, nowMs) : 'without a timestamp'}`)
}

/** The PageHeader chip for the sources a page shows: "<prefix> 2h ago" with the worst state's tone. */
export function freshnessChip(summary: FreshnessSummary | null, nowMs: number, prefix = 'Collected'): Freshness | undefined {
  if (!summary) return undefined
  return {
    text: `${prefix} ${sourceAge(summary, nowMs)}`,
    tone: FRESHNESS_TONE[summary.state],
    detail: `${sourceLines(summary.sources, nowMs).join(' · ')}. The age is the oldest of these collector sources.`,
  }
}

/** 150 -> "2h 30m", 180 -> "3h", 2160 -> "36h". */
function minutesText(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (!hours) return `${rest}m`
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

function target(row: SourceFreshness): string {
  return row.warnMinutes === row.staleMinutes
    ? `stale after ${minutesText(row.staleMinutes)}`
    : `target ${minutesText(row.warnMinutes)} · stale after ${minutesText(row.staleMinutes)}`
}

function summaryStat(label: string, summary: FreshnessSummary | null, nowMs: number): DetailStat {
  return {
    label,
    value: sourceAge(summary, nowMs),
    meta: summary ? `${summary.sources.length} sources` : 'No sources configured',
    tone: summary ? FRESHNESS_TONE[summary.state] : undefined,
    toneLabel: summary ? FRESHNESS_LABEL[summary.state] : undefined,
  }
}

const COLUMNS = (nowMs: number): ColumnSpec<SourceFreshness>[] => [
  { id: 'source', label: 'Source', value: (row) => row.label, cell: (row) => text(row.label, row.area) },
  {
    id: 'state',
    label: 'State',
    value: (row) => (row.informational ? 'Upstream activity' : FRESHNESS_LABEL[row.state]),
    cell: (row) => (row.informational ? badge('Upstream activity', 'neutral') : badge(FRESHNESS_LABEL[row.state], FRESHNESS_TONE[row.state])),
  },
  {
    id: 'collected',
    label: 'Collected',
    value: (row) => row.ageMs ?? Number.MAX_SAFE_INTEGER,
    cell: (row) => (row.timestamp ? text(age(row.timestamp, nowMs), shortDate(row.timestamp)) : text('No timestamp')),
  },
  { id: 'target', label: 'Target', value: (row) => row.staleMinutes, cell: (row) => text(target(row)), hideable: true },
  { id: 'file', label: 'File', value: (row) => row.path ?? '', cell: (row) => mono(row.path), hideable: true },
]

/** The "Data freshness" sheet: every configured source's collection age against its target, worst first. */
export function freshnessDetail(rows: SourceFreshness[], nowMs: number): Detail {
  const collectors = rows.filter((row) => !row.informational)
  const late = collectors.filter((row) => row.state !== 'fresh').length
  return {
    id: 'data-freshness',
    title: 'Data freshness',
    subtitle: 'How long ago each published source was collected, against its target',
    description:
      'Core data is as old as its oldest core source; the queue is tracked separately. Upstream activity (such as the latest AMD nightly) is shown for context and never counts as collection age.',
    stats: [
      summaryStat('Core data', coreFreshness(rows), nowMs),
      summaryStat('Queue', queueFreshness(rows), nowMs),
      { label: 'Sources past target', value: `${late} of ${collectors.length}`, tone: late ? 'soft' : undefined, toneLabel: 'Past target' },
    ],
    sources: [{ label: 'Freshness targets', url: TARGETS_URL }],
    tables: [{ title: 'Sources', meta: `${rows.length} sources`, columns: COLUMNS(nowMs), rows, pageSize: false }],
    width: 'lg',
  }
}
