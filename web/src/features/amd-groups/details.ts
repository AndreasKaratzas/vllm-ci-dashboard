import { markRaw } from 'vue'
import type { Detail } from '@/components/evidence/detail'
import { badge, dotted, drill, link, num, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import {
  amdGroupObservations,
  amdGroupPassRate,
  amdLatestState,
  amdLogicalCatalogGroup,
  amdLogicalSignalLabel,
  amdLogicalSignalTone,
  amdLogicalStateLabel,
  amdLogicalStateTone,
  amdStateLabel,
  isIncidentObservation,
  observationState,
  observationTimestamp,
} from '@/domain/ciHealth'
import { integer, shortDate, toneForState, value, type Rec } from '@/domain/format'
import { hardwareDisplayLabel } from '@/domain/health'
import { exactPipelineEvidenceUrl } from '@/domain/urls'
import { hardwareColor } from '@/lib/palette'
import type { Tone } from '@/lib/tone'
import OutcomeStrip from './OutcomeStrip.vue'

/** What the AMD-group drill-downs need from the page: the amd_test_health section and its signal build. */
export interface AmdGroupContext {
  amdHealth: Rec | null | undefined
  latestBuild: unknown
}

const jobName = (row: Rec) => String(row.display_name || row.name || row.job_name || 'AMD job')

export function hardwareCell(hardware: unknown): CellSpec {
  return dotted(hardwareDisplayLabel(hardware), hardwareColor(hardware))
}

const RESULT_LABEL: Partial<Record<Tone, string>> = { pass: 'Passed', soft: 'Soft fail', hard: 'Hard fail' }

/** One run or route result, named the same way everywhere: Passed, Soft fail, Hard fail, or No signal. */
export function resultLabel(state: unknown): string {
  return RESULT_LABEL[toneForState(state)] ?? 'No signal'
}

export function resultCell(state: unknown): CellSpec {
  const tone = toneForState(state)
  return { text: resultLabel(state), tone: RESULT_LABEL[tone] ? tone : 'neutral' }
}

const plural = (n: unknown, word: string) => `${integer(n)} ${Number(n) === 1 ? word : `${word}s`}`

/** One exact main CI AMD gating job (a Buildkite job variant) and its retained outcomes. */
export function exactJobDetail(row: Rec, latestBuild: unknown): Detail {
  const observations = amdGroupObservations(row)
  const incidents = observations.filter(isIncidentObservation)
  const latest = amdLatestState(row, latestBuild)
  const passRate = amdGroupPassRate(row)
  return {
    id: `amd-job:${row.id || jobName(row)}`,
    title: jobName(row),
    subtitle: `${hardwareDisplayLabel(row.hardware_variant || row.hardware)} · ${value(row.queue)} · exact main CI AMD gating job`,
    stats: [
      {
        label: 'Latest AMD result',
        value: amdStateLabel(latest),
        tone: toneForState(latest),
        badge: amdStateLabel(latest),
        meta: row.latest_build_number ? `#${row.latest_build_number} · ${shortDate(row.latest_observed_at)}` : 'No retained latest result',
      },
      {
        label: 'Retained-run pass rate',
        value: passRate === null ? '—' : `${passRate.toFixed(1)}%`,
        meta: `${integer(row.passed)} passed · ${integer(row.soft_failed)} soft fail · ${integer(row.hard_failed)} hard fail`,
      },
      { label: 'Current pass streak', value: integer(row.current_pass_streak), meta: `of ${integer(row.runs)} retained AMD ${Number(row.runs) === 1 ? 'nightly' : 'nightlies'}` },
      {
        label: 'Non-passing runs',
        value: integer(incidents.length),
        meta: incidents.length ? 'Every failed run links its Buildkite job' : 'None in retained history',
      },
    ],
    sources: row.latest_url ? [{ label: 'Open latest job', url: row.latest_url }] : [],
    sections: observations.length
      ? [
          {
            title: 'Main CI AMD gating-job outcomes',
            meta: `${plural(observations.length, 'exact job run')}, oldest to newest`,
            component: markRaw(OutcomeStrip),
            props: { observations },
          },
        ]
      : [],
    tables: incidents.length
      ? [
          {
            title: 'Failure evidence',
            meta: plural(incidents.length, 'exact AMD job'),
            rows: incidents.slice().reverse(),
            pageSize: 10,
            columns: [
              { id: 'result', label: 'Result', value: (o: Rec) => observationState(o), cell: (o: Rec) => resultCell(observationState(o)) },
              { id: 'observed', label: 'Observed', value: (o: Rec) => String(observationTimestamp(o) || ''), cell: (o: Rec) => text(shortDate(observationTimestamp(o))) },
              { id: 'build', label: 'Build', value: (o: Rec) => Number(o.build_number || 0), cell: (o: Rec) => link(o.build_number ? `#${o.build_number}` : '—', exactPipelineEvidenceUrl(o, 'ci'), true) },
            ] satisfies ColumnSpec<Rec>[],
          },
        ]
      : [],
    width: 'lg',
  }
}

/** A build-pinned logical test group: every hardware route and exact job variant behind it. */
export function logicalGroupDetail(row: Rec, context: AmdGroupContext): Detail {
  const variants: Rec[] = row.job_variants || []
  const routes: Rec[] = row.hardware_states || []
  const exactDetail = (variant: Rec) => {
    const group = amdLogicalCatalogGroup(variant, context.amdHealth)
    return group ? exactJobDetail(group, context.latestBuild) : null
  }
  return {
    id: `amd-logical:${row.logical_key || row.label}`,
    title: String(row.label || row.logical_key || 'Logical AMD test group'),
    subtitle: `${plural(row.hardware_count, 'hardware route')} · ${plural(row.job_variant_count, 'exact job variant')}`,
    stats: [
      { label: 'Latest result', value: amdLogicalStateLabel(row.state), tone: amdLogicalStateTone(row.state), badge: amdLogicalStateLabel(row.state) },
      { label: 'Hardware routes', value: integer(row.hardware_count), meta: routes.map((s) => hardwareDisplayLabel(s.hardware)).join(' · ') || undefined },
      { label: 'Exact job variants', value: integer(row.job_variant_count) },
    ],
    tables: [
      {
        title: 'Exact AMD job variants',
        rows: variants,
        pageSize: 25,
        columns: [
          {
            id: 'variant',
            label: 'Exact AMD job variant',
            value: (v: Rec) => String(v.display_name || v.exact_job_name || ''),
            cell: (v: Rec) => {
              const label = v.display_name || v.exact_job_name || 'Unnamed variant'
              const detail = exactDetail(v)
              return detail ? drill(label, () => detail) : link(label, v.job_url || v.build_url)
            },
          },
          { id: 'hardware', label: 'Hardware route', value: (v: Rec) => hardwareDisplayLabel(v.hardware_variant || v.hardware), cell: (v: Rec) => hardwareCell(v.hardware_variant || v.hardware) },
          { id: 'signal', label: 'Test signal', value: (v: Rec) => amdLogicalSignalLabel(v.test_signal_state), cell: (v: Rec) => badge(amdLogicalSignalLabel(v.test_signal_state), amdLogicalSignalTone(v.test_signal_state)) },
          {
            id: 'terminal',
            label: 'Terminal job',
            value: (v: Rec) => amdStateLabel(amdLatestState({ latest_state: v.terminal_state }, null)),
            cell: (v: Rec) => badge(amdStateLabel(amdLatestState({ latest_state: v.terminal_state }, null)), toneForState(v.terminal_state)),
          },
          { id: 'tests', label: 'Tests passed', numeric: true, value: (v: Rec) => Number(v.passed_tests || 0), cell: (v: Rec) => text(`${integer(v.passed_tests)} / ${integer(v.tests)}`) },
          { id: 'evidence', label: 'Evidence', sortable: false, value: () => '', cell: (v: Rec) => (v.job_url || v.build_url ? link('Open exact job', v.job_url || v.build_url) : text(null)) },
        ] satisfies ColumnSpec<Rec>[],
      },
    ],
    width: 'xl',
  }
}

function routeOutcomes(row: Rec): string {
  return (row.hardware_states || []).map((s: Rec) => `${hardwareDisplayLabel(s.hardware)} ${amdLogicalSignalLabel(s.state).toLowerCase()}`).join(' · ')
}

/** Columns of the logical test-group catalog; each group drills into its routes and exact jobs. */
export function logicalCatalogColumns(context: AmdGroupContext): ColumnSpec<Rec>[] {
  return [
    {
      id: 'label',
      label: 'Logical AMD test group',
      value: (row) => String(row.label || row.logical_key || ''),
      cell: (row) => drill(row.label || row.logical_key, () => logicalGroupDetail(row, context)),
      hideable: false,
    },
    { id: 'state', label: 'Latest result', value: (row) => amdLogicalStateLabel(row.state), cell: (row) => badge(amdLogicalStateLabel(row.state), amdLogicalStateTone(row.state)) },
    { id: 'hardware', label: 'Hardware routes', numeric: true, value: (row) => Number(row.hardware_count || 0), cell: (row) => num(Number(row.hardware_count || 0)) },
    { id: 'variants', label: 'Exact job variants', numeric: true, value: (row) => Number(row.job_variant_count || 0), cell: (row) => num(Number(row.job_variant_count || 0)) },
    { id: 'routes', label: 'Route outcomes', sortable: false, value: routeOutcomes, cell: (row) => text(routeOutcomes(row) || null) },
  ]
}

/** The build-pinned logical catalog (or one outcome slice of it). */
export function logicalCatalogDetail(title: string, rows: Rec[], context: AmdGroupContext): Detail {
  return {
    id: `amd-logical-catalog:${title}`,
    title,
    subtitle: `Build-pinned logical test groups from latest observed AMD test signal #${value(context.latestBuild)}`,
    tables: [
      {
        rows,
        columns: logicalCatalogColumns(context),
        rowDetail: (row: Rec) => logicalGroupDetail(row, context),
        searchPlaceholder: 'Filter logical test group, result, or hardware',
        emptyText: 'No logical test groups in this slice.',
        pageSize: 25,
      },
    ],
    width: 'xl',
  }
}

/** Columns of an exact job-variant list; each job opens its evidence. */
export function exactJobColumns(latestBuild: unknown): ColumnSpec<Rec>[] {
  return [
    {
      id: 'variant',
      label: 'AMD job variant',
      value: (row) => [row.display_name, row.name, row.hardware_variant, row.queue].join(' '),
      cell: (row) => ({ ...drill(jobName(row), () => exactJobDetail(row, latestBuild)), sub: value(row.queue, 'queue unavailable') }),
      hideable: false,
    },
    { id: 'hardware', label: 'Hardware', value: (row) => hardwareDisplayLabel(row.hardware_variant || row.hardware), cell: (row) => hardwareCell(row.hardware_variant || row.hardware) },
    { id: 'latest', label: 'Latest', value: (row) => amdLatestState(row, latestBuild), cell: (row) => badge(amdStateLabel(amdLatestState(row, latestBuild)), toneForState(amdLatestState(row, latestBuild))) },
    {
      id: 'build',
      label: 'Build',
      value: (row) => Number(row.latest_build_number || 0),
      cell: (row) => link(row.latest_build_number ? `#${row.latest_build_number}` : '—', row.latest_url, true),
    },
  ]
}
