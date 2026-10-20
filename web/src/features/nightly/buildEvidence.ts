import type { Detail, DetailNote, DetailStat } from '@/components/evidence/detail'
import { badge, link, mono, type ColumnSpec } from '@/components/table/columns'
import { integer, shortDate, toneForState, value, type Rec, type Tone } from '@/domain/format'
import { nightlyBuildEvidence, nightlyFailureMovement } from '@/domain/nightly'
import { commitUrl } from '@/domain/perf'
import { exactPipelineBuildUrl, exactPipelineEvidenceUrl } from '@/domain/urls'
import { hardwareColor } from '@/lib/palette'

export interface BuildEvidenceOptions {
  /** Sheet title; defaults to "AMD nightly #<number>". */
  title?: string
  /** A severity ('hard' | 'soft') or movement bucket ('new' | 'recurring' | 'fixed'); anything else shows all movement. */
  scope?: string
  /** Buildkite pipeline whose URLs count as exact evidence. Defaults to the build's own source pipeline. */
  pipeline?: string
}

const CHANGE_TONE: Record<string, Tone> = { 'New failure': 'hard', 'Recurring failure': 'hard', Fixed: 'pass' }

function failureCount(build: Rec, key: 'failed_groups' | 'soft_failed_groups'): number {
  return Array.isArray(build[key]) ? build[key].length : 0
}

/** The evidence sheet for one nightly build: its outcome, failure movement and the job variants behind a count. */
export function buildEvidenceDetail(build: Rec, options: BuildEvidenceOptions = {}): Detail {
  const pipeline = options.pipeline || build.source_pipeline || 'ci'
  const evidence = nightlyBuildEvidence(build, options.scope)
  const scoped = evidence.scope !== 'movement'
  const severityScope = evidence.scope === 'hard' || evidence.scope === 'soft'
  const movement = nightlyFailureMovement(build)
  const movementAvailable = !!movement && movement.available !== false
  const hasSignal = build.has_test_results !== false
  const blocked = Number(build.test_jobs_blocked || 0)

  const movementCount = (key: 'new' | 'recurring' | 'fixed') => (movementAvailable && Array.isArray(movement![key]) ? movement![key].length : null)
  const countStat = (label: string, count: number | null, tone?: Tone, toneLabel?: string): DetailStat => ({
    label,
    value: count === null ? 'Unavailable' : integer(count),
    tone: count ? tone : undefined,
    toneLabel: count ? toneLabel : undefined,
  })
  const stats: DetailStat[] = [{ label: 'Job variants observed', value: integer(build.total_groups) }]
  // A hard or soft scope lists current failures; every other scope is about movement against the previous nightly.
  if (severityScope) {
    stats.push(countStat('Hard failures', failureCount(build, 'failed_groups')), countStat('Soft failures', failureCount(build, 'soft_failed_groups')))
  } else if (hasSignal) {
    stats.push(
      countStat('New failures', movementCount('new'), 'hard', 'New'),
      countStat('Recurring failures', movementCount('recurring')),
      countStat('Fixed', movementCount('fixed')),
    )
  }

  const notes: DetailNote[] = []
  if (!hasSignal) {
    notes.push({
      tone: blocked ? 'hard' : 'soft',
      text: blocked
        ? `This nightly failed before test execution. ${integer(blocked)} test jobs were dependency-blocked, so no pass/fail movement is inferred.`
        : 'This build has no parsed test-group signal. No pass/fail movement is inferred.',
    })
  }
  if (!evidence.available) {
    notes.push({
      tone: 'soft',
      text: scoped
        ? `${evidence.label} evidence is unavailable for this build.`
        : 'Failure movement is unavailable for this build. The build outcome above is still the source result.',
    })
  }

  const columns: ColumnSpec<Rec>[] = [
    {
      id: 'variant',
      label: 'Job variant',
      value: (row) => String(row.display_name || row.name || ''),
      cell: (row) => link(row.display_name || row.name, exactPipelineEvidenceUrl(row, pipeline)),
    },
    severityScope
      ? {
          id: 'result',
          label: 'Current result',
          value: (row) => value(row.current_state || row.state || row.result),
          cell: (row) => badge(value(row.current_state || row.state || row.result), toneForState(row.current_state || row.state || row.result)),
        }
      : {
          id: 'change',
          label: 'Change',
          value: (row) => String(row.lifecycle || ''),
          cell: (row) => badge(row.lifecycle, CHANGE_TONE[row.lifecycle] ?? 'neutral'),
        },
    {
      id: 'queue',
      label: 'Queue',
      value: (row) => String(row.queue || ''),
      cell: (row) => (row.queue ? { ...mono(row.queue), dots: [hardwareColor(row.queue)] } : mono(row.queue)),
      // Phones keep the job and its change; the queue is in the job's own evidence.
      class: 'hidden whitespace-nowrap sm:table-cell',
    },
  ]

  const commit = typeof build.commit === 'string' ? build.commit : ''
  const state = String(build.state || '')
  const message = typeof build.message === 'string' ? build.message.split('\n')[0]!.trim() : ''
  const buildUrl = exactPipelineBuildUrl(build, pipeline)
  const label = scoped ? evidence.label : 'Failure movement'

  return {
    id: `build-${value(build.number)}-${evidence.scope}`,
    title: options.title || (build.number ? `AMD nightly #${build.number}` : 'AMD nightly'),
    subtitle: [shortDate(build.created_at), scoped ? `${evidence.label} evidence` : 'failure movement against the previous nightly']
      .filter((part) => part && part !== '-')
      .join(' · '),
    notes,
    stats,
    fields: [
      { label: 'Buildkite state', value: state ? state.charAt(0).toUpperCase() + state.slice(1) : null, tone: toneForState(state) },
      { label: 'Current failures', value: hasSignal && !severityScope ? `${integer(failureCount(build, 'failed_groups'))} hard · ${integer(failureCount(build, 'soft_failed_groups'))} soft` : null },
      { label: 'Message', value: message || null },
      { label: 'Commit', value: commit ? commit.slice(0, 12) : null, href: commit ? commitUrl(commit) : undefined, mono: true },
      { label: 'Test signal', value: hasSignal ? 'Observed' : 'Unavailable' },
      { label: 'Dependency-blocked test jobs', value: blocked ? integer(blocked) : null },
    ],
    sources: buildUrl ? [{ label: 'Open Buildkite build', url: buildUrl }] : [],
    tables: evidence.rows.length
      ? [
          {
            title: label,
            meta: `${integer(evidence.rows.length)} job variants`,
            columns,
            rows: evidence.rows,
            searchText: (row: Rec) => [row.display_name, row.name, row.lifecycle, row.queue].join(' '),
          },
        ]
      : [],
    empty:
      !evidence.rows.length && evidence.available
        ? scoped
          ? `No ${evidence.label.toLowerCase()} are observed in this build.`
          : 'No failure movement is observed in this build.'
        : undefined,
    width: 'lg',
  }
}
