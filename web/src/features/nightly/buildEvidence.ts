import type { Detail, DetailNote, DetailStat } from '@/components/evidence/detail'
import { badge, link, mono, type ColumnSpec } from '@/components/table/columns'
import { integer, shortDate, toneForState, value, type Rec, type Tone } from '@/domain/format'
import { nightlyBuildEvidence, nightlyFailureMovement } from '@/domain/nightly'
import { exactPipelineBuildUrl, exactPipelineEvidenceUrl } from '@/domain/urls'

export interface BuildEvidenceOptions {
  /** Sheet title; defaults to "AMD build #<number>". */
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

  const stats: DetailStat[] = [{ label: 'Job variants observed', value: integer(build.total_groups) }]
  if (hasSignal) {
    stats.push({ label: 'Hard failures', value: integer(failureCount(build, 'failed_groups')) })
    stats.push({ label: 'Soft failures', value: integer(failureCount(build, 'soft_failed_groups')) })
  }
  stats.push(
    scoped
      ? { label: evidence.label, value: evidence.available ? integer(evidence.rows.length) : 'Unavailable' }
      : {
          label: 'New / recurring / fixed',
          value: movementAvailable
            ? `${integer((movement!.new || []).length)} / ${integer((movement!.recurring || []).length)} / ${integer((movement!.fixed || []).length)}`
            : 'Unavailable',
        },
  )

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
    { id: 'queue', label: 'Queue', value: (row) => String(row.queue || ''), cell: (row) => mono(row.queue) },
  ]

  const commit = typeof build.commit === 'string' ? build.commit : ''
  const message = typeof build.message === 'string' ? build.message.split('\n')[0]!.trim() : ''
  const buildUrl = exactPipelineBuildUrl(build, pipeline)
  const label = scoped ? evidence.label : 'Failure movement'

  return {
    id: `build-${value(build.number)}-${evidence.scope}`,
    title: options.title || (build.number ? `AMD build #${build.number}` : 'AMD build'),
    subtitle: scoped ? `${evidence.label} evidence` : 'Build result and failure movement evidence',
    description: message || undefined,
    notes,
    stats,
    fields: [
      { label: 'State', value: value(build.state) },
      { label: 'Started', value: shortDate(build.created_at) },
      { label: 'Commit', value: commit ? commit.slice(0, 12) : null },
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
