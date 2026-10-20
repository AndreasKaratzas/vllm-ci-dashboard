import type { Detail, DetailStat } from '@/components/evidence/detail'
import type { Freshness } from '@/components/layout/PageHeader.vue'
import { badge, mono, text, type ColumnSpec } from '@/components/table/columns'
import { shortDay } from '@/components/charts/chart'
import { age, reasonText } from '@/domain/format'
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


/** 150 -> "2h 30m", 180 -> "3h", 2160 -> "36h". */
function minutesText(minutes: number): string {
  const hours = Math.floor(minutes / 60)
  const rest = minutes % 60
  if (!hours) return `${rest}m`
  return rest ? `${hours}h ${rest}m` : `${hours}h`
}

/** "2h 30m / 3h" (target / stale), or just the stale limit when both match. */
function target(row: SourceFreshness): string {
  return row.warnMinutes === row.staleMinutes ? minutesText(row.staleMinutes) : `${minutesText(row.warnMinutes)} / ${minutesText(row.staleMinutes)}`
}

/** "2h ago", or why a source has no collection time. */
function collected(row: SourceFreshness, nowMs: number): string {
  if (row.timestamp) return age(row.timestamp, nowMs)
  return row.unavailableReason ? `unavailable: ${reasonText(row.unavailableReason).toLowerCase()}` : 'without a timestamp'
}

/** One line per source: "CI health 2h ago (target 2h 30m / 3h)". */
export function sourceLines(sources: SourceFreshness[], nowMs: number): string[] {
  return sources.map((row) => `${row.label} ${collected(row, nowMs)} (target ${target(row)})`)
}

/** " · 1 missing" when some sources have no timestamp but others do. */
export function missingSuffix(summary: Pick<FreshnessSummary, 'timestamp' | 'missing'>): string {
  return summary.timestamp && summary.missing.length ? ` · ${summary.missing.length} missing` : ''
}

/** The PageHeader chip for the sources a page shows: "<prefix> 2h ago" with the worst state's tone. */
export function freshnessChip(summary: FreshnessSummary | null, nowMs: number, prefix = 'Collected'): Freshness | undefined {
  if (!summary) return undefined
  const oldest = summary.sources.length > 1 ? ' The age is the oldest of these collector sources that have a timestamp.' : ''
  return {
    text: summary.timestamp ? `${prefix} ${sourceAge(summary, nowMs)}${missingSuffix(summary)}` : `${prefix} at an unknown time`,
    tone: FRESHNESS_TONE[summary.state],
    detail: `${sourceLines(summary.sources, nowMs).join(' · ')}.${oldest}`,
  }
}

const plural = (count: number, noun: string) => `${count} ${noun}${count === 1 ? '' : 's'}`

function summaryStat(label: string, summary: FreshnessSummary | null, nowMs: number): DetailStat {
  return {
    label,
    value: sourceAge(summary, nowMs),
    meta: summary ? plural(summary.sources.length, 'source') : 'No sources configured',
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
    cell: (row) =>
      row.timestamp
        ? text(age(row.timestamp, nowMs), shortDay(Date.parse(row.timestamp)))
        : text(row.unavailableReason ? 'Unavailable' : 'No timestamp', row.unavailableReason ? reasonText(row.unavailableReason) : undefined),
    class: 'whitespace-nowrap',
  },
  {
    id: 'target',
    label: 'Target / stale',
    value: (row) => row.staleMinutes,
    cell: (row) => text(target(row)),
    hideable: true,
    class: 'hidden whitespace-nowrap sm:table-cell',
  },
  { id: 'file', label: 'File', value: (row) => row.path ?? '', cell: (row) => mono(row.path), hideable: true, class: 'hidden lg:table-cell' },
]

/** The "Data freshness" sheet: every configured source's collection age against its target, worst first. */
export function freshnessDetail(rows: SourceFreshness[], nowMs: number): Detail {
  const collectors = rows.filter((row) => !row.informational)
  const lateRows = collectors.filter((row) => row.state !== 'fresh')
  const late = lateRows.length
  const worst: FreshnessState = lateRows.some((row) => row.state === 'stale') ? 'stale' : late ? 'warn' : 'fresh'
  return {
    id: 'data-freshness',
    title: 'Data freshness',
    subtitle: 'How long ago each published source was collected, against its target',
    description:
      'Core data is as old as its oldest core source; the queue is tracked separately. Upstream activity (such as the latest AMD nightly) is shown for context and never counts as collection age.',
    stats: [
      summaryStat('Core data', coreFreshness(rows), nowMs),
      summaryStat('Queue', queueFreshness(rows), nowMs),
      {
        label: 'Sources past target',
        value: `${late} of ${collectors.length}`,
        meta: late ? `${lateRows.filter((row) => row.state === 'stale').length} stale` : undefined,
        tone: late ? FRESHNESS_TONE[worst] : undefined,
        toneLabel: late ? FRESHNESS_LABEL[worst] : undefined,
      },
    ],
    sources: [{ label: 'Freshness targets', url: TARGETS_URL }],
    tables: [
      {
        title: 'Sources',
        meta: `${plural(collectors.length, 'collector source')}${rows.length > collectors.length ? ` · ${rows.length - collectors.length} upstream activity, not counted` : ''}`,
        columns: COLUMNS(nowMs),
        rows,
        pageSize: false,
      },
    ],
    width: 'lg',
  }
}
