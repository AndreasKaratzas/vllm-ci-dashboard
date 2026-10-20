import type { Detail, DetailField, DetailStat } from '@/components/evidence/detail'
import { shortDayTime } from '@/components/charts/chart'
import { badge, hardwareCell, link, num, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import { integer, shortDate, toneForState } from '@/domain/format'
import {
  buildOutcome,
  buildTone,
  eventLabel,
  eventTone,
  groupLabel,
  mainTimeline,
  passRate,
  type FlakyGroup,
  type MainBuild,
  type MainIncident,
  type RetryGroup,
  type TimelineEvent,
  type TrendPoint,
} from '@/domain/mainCi'
import { commitUrl } from '@/domain/perf'
import { rateTone, TONE_LABEL } from '@/lib/palette'
import type { Rec } from '@/domain/format'
import FailingGroups from './FailingGroups.vue'

/** What the main CI sheets look up besides their own row: the section record and its builds. */
export interface MainCiContext {
  record: Rec | null
  builds: MainBuild[]
}

const percentText = (value: number | null) => (value === null ? '—' : `${value.toFixed(1)}%`)
const shortSha = (sha: string) => sha.slice(0, 12)

const countCell = (count: number, tone?: 'hard' | 'soft'): CellSpec => ({ text: integer(count), muted: !count, tone: count ? tone : undefined })

/** A build row's link to its own sheet when the section still has it, else to Buildkite. */
function buildCell(number: number | null, url: string, ctx: MainCiContext): CellSpec {
  if (!number) return text(null)
  const build = ctx.builds.find((row) => row.number === number)
  return build ? { text: `#${number}`, mono: true, detail: () => mainBuildDetail(build, ctx) } : link(`#${number}`, url, true)
}

export function buildColumns(): ColumnSpec<MainBuild>[] {
  return [
    { id: 'build', label: 'Build', value: (row) => row.number, cell: (row) => link(`#${row.number}`, row.url, true), class: 'whitespace-nowrap' },
    {
      id: 'created',
      label: 'Created',
      value: (row) => row.createdAt,
      cell: (row) => ({ text: shortDayTime(Date.parse(row.createdAt)), sub: row.nightly ? 'Nightly' : undefined, nowrap: true }),
      class: 'hidden sm:table-cell',
    },
    {
      id: 'commit',
      label: 'Commit',
      value: (row) => row.headline,
      cell: (row) => ({ text: row.headline || shortSha(row.commit) || '—', sub: row.commit ? shortSha(row.commit) : undefined }),
      class: 'hidden max-w-md md:table-cell',
    },
    {
      id: 'outcome',
      label: 'AMD result',
      value: (row) => buildOutcome(row),
      cell: (row) => badge(buildOutcome(row), buildTone(row)),
      class: 'whitespace-nowrap',
    },
    { id: 'passed', label: 'Passed', numeric: true, value: (row) => row.amd?.passed ?? -1, cell: (row) => countCell(row.amd?.passed ?? 0), class: 'hidden sm:table-cell' },
    { id: 'hard', label: 'Hard', numeric: true, value: (row) => row.amd?.hard ?? -1, cell: (row) => countCell(row.amd?.hard ?? 0, 'hard') },
    { id: 'soft', label: 'Soft', numeric: true, value: (row) => row.amd?.soft ?? -1, cell: (row) => countCell(row.amd?.soft ?? 0, 'soft'), class: 'hidden sm:table-cell' },
    { id: 'retried', label: 'Retried', numeric: true, value: (row) => row.amd?.retried ?? -1, cell: (row) => countCell(row.amd?.retried ?? 0), class: 'hidden sm:table-cell' },
  ]
}

export const buildSearch = (row: MainBuild) => [row.number, row.headline, row.commit, buildOutcome(row), row.nightly ? 'nightly' : ''].join(' ')

/** Event columns; a group's own sheet drops the group, a build's own sheet the build and time. */
function eventColumns(ctx: MainCiContext, { group = true, build = true }: { group?: boolean; build?: boolean } = {}): ColumnSpec<TimelineEvent>[] {
  const columns: ColumnSpec<TimelineEvent>[] = [
    { id: 'event', label: 'Event', value: (row) => row.event, cell: (row) => badge(eventLabel(row.event), eventTone(row)), class: 'whitespace-nowrap' },
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => text(groupLabel(row.name)) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'build', label: 'Build', value: (row) => row.build ?? 0, cell: (row) => buildCell(row.build, row.buildUrl, ctx), class: 'whitespace-nowrap' },
    { id: 'at', label: 'When', value: (row) => row.at, cell: (row) => text(row.at ? shortDayTime(Date.parse(row.at)) : null), class: 'hidden whitespace-nowrap md:table-cell' },
  ]
  return columns.filter((column) => (group || column.id !== 'group') && (build || (column.id !== 'build' && column.id !== 'at')))
}

const countStat = (label: string, count: number, tone?: 'hard' | 'soft', meta?: string): DetailStat => ({
  label,
  value: integer(count),
  tone: count ? tone : undefined,
  meta,
})

/** The evidence sheet of one main build: AMD outcome, its failing groups from the run ledger, and incident events. */
export function mainBuildDetail(build: MainBuild, ctx: MainCiContext): Detail {
  const amd = build.amd
  const failing = amd ? amd.hard + amd.soft : 0
  const events = mainTimeline(ctx.record).filter((event) => event.build === build.number)
  const rate = passRate(amd)
  const stats: DetailStat[] = amd
    ? [
        { label: 'AMD pass rate', value: percentText(rate), tone: rate === null ? undefined : rateTone(rate), toneLabel: rate === null ? undefined : TONE_LABEL[rateTone(rate)], meta: `${integer(amd.passed)} of ${integer(amd.passed + amd.soft + amd.hard)} jobs` },
        countStat('Hard failures', amd.hard, 'hard'),
        countStat('Soft failures', amd.soft, 'soft'),
        countStat('Retried', amd.retried, undefined, 'jobs Buildkite ran again'),
      ]
    : []
  const fields: DetailField[] = [
    { label: 'Buildkite state', value: build.state.charAt(0).toUpperCase() + build.state.slice(1), tone: toneForState(build.state) },
    { label: 'Created', value: shortDate(build.createdAt) },
    { label: 'Kind', value: build.nightly ? 'Scheduled nightly' : 'Main commit' },
    { label: 'Message', value: build.headline || null },
    { label: 'Commit', value: build.commit ? shortSha(build.commit) : null, href: build.commit ? commitUrl(build.commit) : undefined, mono: true },
    { label: 'Not run / unknown', value: amd && amd.notRun + amd.unknown ? `${integer(amd.notRun)} / ${integer(amd.unknown)}` : null },
  ]
  return {
    id: `main-build-${build.number}`,
    title: `Main build #${build.number}`,
    subtitle: [shortDate(build.createdAt), buildOutcome(build)].join(' · '),
    notes: amd ? [] : [{ tone: 'neutral', text: 'This build recorded no AMD MI job counts; open it on Buildkite for its jobs.' }],
    stats,
    fields,
    sources: [
      ...(build.url ? [{ label: 'Open Buildkite build', url: build.url }] : []),
      ...(build.commit ? [{ label: 'Open commit', url: commitUrl(build.commit) }] : []),
    ],
    sections: failing ? [{ title: 'Failing AMD groups', meta: `${integer(failing)} in this build`, component: FailingGroups, props: { build } }] : [],
    tables: events.length
      ? [{ title: 'Incident events at this build', meta: `${integer(events.length)} events`, columns: eventColumns(ctx, { build: false }), rows: events, pageSize: 10 }]
      : [],
    empty: amd && !failing ? 'Every AMD job with a verdict passed in this build.' : undefined,
    width: 'lg',
  }
}

/** Incident lifecycle events with their group, for the timeline table. */
export const timelineColumns = (ctx: MainCiContext) => eventColumns(ctx)

export function incidentColumns(ctx: MainCiContext): ColumnSpec<MainIncident>[] {
  return [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    {
      id: 'severity',
      label: 'Severity',
      value: (row) => row.severity,
      cell: (row) => badge(row.pending ? 'Pending soft' : TONE_LABEL[row.severity], row.severity),
      class: 'whitespace-nowrap',
    },
    { id: 'since', label: 'First bad', value: (row) => row.bad.build ?? row.latestBuild ?? 0, cell: (row) => buildCell(row.bad.build ?? row.latestBuild, row.latestBuildUrl, ctx), class: 'hidden whitespace-nowrap sm:table-cell' },
    {
      id: 'range',
      label: 'Commit range',
      value: (row) => row.compareUrl,
      cell: (row) =>
        row.compareUrl
          ? link(`${shortSha(row.good.commit).slice(0, 7)}…${shortSha(row.bad.commit).slice(0, 7)}`, row.compareUrl, true)
          : text(row.rangeStatus ? row.rangeStatus.replace(/_/g, ' ') : null),
      class: 'hidden lg:table-cell',
    },
    { id: 'observed', label: 'Last seen', value: (row) => row.observedAt, cell: (row) => text(row.observedAt ? shortDayTime(Date.parse(row.observedAt)) : null), class: 'hidden whitespace-nowrap md:table-cell' },
  ]
}

export const incidentSearch = (row: MainIncident) => [row.name, row.hardware, row.queue, row.severity, row.bad.build, row.good.build].join(' ')

/** One main-failure incident: the good and bad commits that bound it, its bisect command, and its events. */
export function incidentDetail(incident: MainIncident, ctx: MainCiContext, issue: { number: number; url: string } | null): Detail {
  const events = mainTimeline(ctx.record).filter((event) => event.groupId === incident.groupId)
  const buildRef = (number: number | null) => (number ? `#${number}` : null)
  return {
    id: `main-incident-${incident.groupId}`,
    title: groupLabel(incident.name),
    subtitle: [incident.pending ? 'Pending soft failure on main' : `${TONE_LABEL[incident.severity]} incident on main`, incident.hardware.toUpperCase(), incident.queue]
      .filter(Boolean)
      .join(' · '),
    notes: incident.pending ? [{ tone: 'soft', text: 'A soft failure is confirmed after it repeats in a second build; until then it is pending.' }] : [],
    fields: [
      { label: 'Severity', value: incident.pending ? 'Pending soft' : TONE_LABEL[incident.severity], tone: incident.severity },
      { label: 'Last good build', value: buildRef(incident.good.build) },
      { label: 'Good commit', value: incident.good.commit ? shortSha(incident.good.commit) : null, href: incident.good.commit ? commitUrl(incident.good.commit) : undefined, mono: true },
      { label: 'First bad build', value: buildRef(incident.bad.build) },
      { label: 'Bad commit', value: incident.bad.commit ? shortSha(incident.bad.commit) : null, href: incident.bad.commit ? commitUrl(incident.bad.commit) : undefined, mono: true },
      { label: 'Range', value: incident.rangeStatus ? incident.rangeStatus.replace(/_/g, ' ') : null },
      { label: 'Bisect', value: incident.bisectCommand || null, mono: true },
      { label: 'Latest failure', value: incident.observedAt ? shortDate(incident.observedAt) : null },
    ],
    sources: [
      ...(incident.jobUrl ? [{ label: 'Open failing job', url: incident.jobUrl }] : []),
      ...(incident.compareUrl ? [{ label: 'Compare commits', url: incident.compareUrl }] : []),
      ...(issue ? [{ label: `Managed issue #${issue.number}`, url: issue.url }] : []),
    ],
    tables: events.length ? [{ title: 'Incident events', meta: `${integer(events.length)} events`, columns: eventColumns(ctx, { group: false }), rows: events, pageSize: 10 }] : [],
    width: 'lg',
  }
}

/** The main builds behind one point of the pass-rate trend. */
export function trendPointDetail(point: TrendPoint, builds: MainBuild[], label: string, ctx: MainCiContext): Detail {
  return {
    id: `main-trend-${point.from}`,
    title: `Main builds · ${label}`,
    subtitle: `${integer(point.builds)} builds · ${integer(point.greenBuilds)} without a hard AMD failure`,
    stats: [
      { label: 'AMD pass rate', value: percentText(point.passRate), tone: point.passRate === null ? undefined : rateTone(point.passRate), toneLabel: point.passRate === null ? undefined : TONE_LABEL[rateTone(point.passRate)] },
      countStat('Passed jobs', point.passed),
      countStat('Hard failures', point.hard, 'hard'),
      countStat('Soft failures', point.soft, 'soft'),
    ],
    tables: [
      {
        title: 'Builds',
        meta: builds.length === point.builds ? undefined : `${integer(builds.length)} of ${integer(point.builds)} builds are in the published rows`,
        columns: buildColumns(),
        rows: builds,
        searchText: buildSearch,
        onRowClick: (row: MainBuild) => mainBuildDetail(row, ctx),
        pageSize: 15,
      },
    ],
    empty: builds.length ? undefined : 'No main build was created in this interval.',
    width: 'xl',
  }
}

const lastBuildCell = (ref: { number: number; at: string; url: string } | null, ctx: MainCiContext): CellSpec =>
  ref ? { ...buildCell(ref.number, ref.url, ctx), sub: ref.at ? shortDayTime(Date.parse(ref.at)) : undefined } : text(null)

const alertCell = (alerting: boolean): CellSpec => (alerting ? badge('Alerting', 'soft') : { text: 'Below threshold', muted: true })

export function flakyColumns(ctx: MainCiContext): ColumnSpec<FlakyGroup>[] {
  return [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'alert', label: 'Alert', value: (row) => (row.alerting ? 1 : 0), cell: (row) => alertCell(row.alerting), class: 'hidden whitespace-nowrap sm:table-cell' },
    { id: 'flips', label: 'Flips', numeric: true, value: (row) => row.flips, cell: (row) => ({ ...num(row.flips), sub: `prev ${integer(row.previousFlips)}` }) },
    { id: 'flips30', label: '30 days', numeric: true, value: (row) => row.flips30d, cell: (row) => ({ ...num(row.flips30d), sub: `${integer(row.runs30d)} runs` }), class: 'hidden md:table-cell' },
    { id: 'rate', label: 'Pass rate', numeric: true, value: (row) => row.passRate ?? -1, cell: (row) => ({ text: percentText(row.passRate), sub: `30d ${percentText(row.passRate30d)}` }), class: 'hidden sm:table-cell' },
    { id: 'last', label: 'Last failure', value: (row) => row.lastFailure?.number ?? 0, cell: (row) => lastBuildCell(row.lastFailure, ctx), class: 'hidden whitespace-nowrap lg:table-cell' },
  ]
}

export function retryColumns(ctx: MainCiContext): ColumnSpec<RetryGroup>[] {
  return [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'alert', label: 'Alert', value: (row) => (row.alerting ? 1 : 0), cell: (row) => alertCell(row.alerting), class: 'hidden whitespace-nowrap sm:table-cell' },
    { id: 'recovered', label: 'Recoveries', numeric: true, value: (row) => row.recovered, cell: (row) => ({ ...num(row.recovered), sub: `prev ${integer(row.previousRecovered)}` }) },
    { id: 'recovered30', label: '30 days', numeric: true, value: (row) => row.recovered30d, cell: (row) => ({ ...num(row.recovered30d), sub: `${integer(row.runs30d)} runs` }), class: 'hidden md:table-cell' },
    { id: 'last', label: 'Last recovery', value: (row) => row.lastRecovery?.number ?? 0, cell: (row) => lastBuildCell(row.lastRecovery, ctx), class: 'hidden whitespace-nowrap lg:table-cell' },
  ]
}

export const groupSearch = (row: { name: string; hardware: string; queue: string }) => [row.name, row.hardware, row.queue].join(' ')

/** One flaky or retry-recovering group: its window statistics and the build behind its latest event. */
export function groupStatsDetail(row: FlakyGroup | RetryGroup, ctx: MainCiContext, windowDays: number | null): Detail {
  const flaky = 'flips' in row
  const window = windowDays ? `${windowDays} days` : 'window'
  const stats: DetailStat[] = flaky
    ? [
        { label: `Flips (${window})`, value: integer(row.flips), meta: `previous ${window}: ${integer(row.previousFlips)}` },
        { label: 'Flips (30 days)', value: integer(row.flips30d), meta: `${integer(row.runs30d)} runs` },
        { label: `Pass rate (${window})`, value: percentText(row.passRate), tone: row.passRate === null ? undefined : rateTone(row.passRate), toneLabel: row.passRate === null ? undefined : TONE_LABEL[rateTone(row.passRate)] },
        { label: 'Failures (30 days)', value: `${integer(row.hard30d)} hard · ${integer(row.soft30d)} soft` },
      ]
    : [
        { label: `Recoveries (${window})`, value: integer(row.recovered), meta: `previous ${window}: ${integer(row.previousRecovered)}` },
        { label: 'Recoveries (30 days)', value: integer(row.recovered30d), meta: `${integer(row.runs30d)} runs` },
      ]
  const last = flaky ? row.lastFailure : row.lastRecovery
  const lastBuild = last ? ctx.builds.find((build) => build.number === last.number) : undefined
  return {
    id: `main-group-${flaky ? 'flaky' : 'retry'}-${row.groupId}`,
    title: groupLabel(row.name),
    subtitle: [flaky ? 'Outcome flips on main' : 'Passes only after a retry on main', row.hardware.toUpperCase(), row.queue].filter(Boolean).join(' · '),
    description: flaky
      ? 'A flip is a change between pass and fail in consecutive main builds that ran the group; a pass after a retried hard failure counts as a pass.'
      : 'A recovery is a hard-failed attempt that Buildkite retried and that then passed in the same build.',
    notes: row.alerting ? [{ tone: 'soft', title: 'Alerting', text: 'This group crosses the flakiness watcher threshold in the current window.' }] : [],
    stats,
    fields: [
      { label: 'Trend', value: row.trend.charAt(0).toUpperCase() + row.trend.slice(1) },
      { label: flaky ? 'Last failure' : 'Last recovery', value: last ? `#${last.number} · ${shortDate(last.at)}` : null, href: last?.url || undefined },
    ],
    sources: last?.url ? [{ label: `Open build #${last.number}`, url: last.url }] : [],
    tables: lastBuild
      ? [{ title: flaky ? 'Build of the last failure' : 'Build of the last recovery', columns: buildColumns(), rows: [lastBuild], onRowClick: (build: MainBuild) => mainBuildDetail(build, ctx), pageSize: false }]
      : [],
    width: 'lg',
  }
}

