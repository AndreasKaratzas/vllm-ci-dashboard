import { rateStat, type Detail, type DetailField, type DetailNote, type DetailStat } from '@/components/evidence/detail'
import { shortDayTime } from '@/components/charts/chart'
import { badge, hardwareCell, link, rateCell, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import { age, counted, integer, rateText, sentence, shortDate, shortSha, toneForState, type Rec } from '@/domain/format'
import {
  buildOutcome,
  buildTone,
  concentrationText,
  dayRateStatus,
  eventTone,
  failingGroups,
  groupLabel,
  isNewIncident,
  mainTimeline,
  passRate,
  rangeText,
  type FailingGroup,
  type FlakinessThresholds,
  type FlakyGroup,
  type MainBuild,
  type MainIncident,
  type RetryGroup,
  type SuspectRange,
  type TimelineEvent,
  type TrendPoint,
} from '@/domain/mainCi'
import { commitUrl } from '@/domain/perf'
import { TONE_LABEL } from '@/lib/palette'

/** What the main CI sheets look up besides their own row: the section record and its builds. */
export interface MainCiContext {
  record: Rec | null
  builds: MainBuild[]
}

const at = (iso: string) => (iso ? shortDayTime(Date.parse(iso)) : null)

const countCell = (count: number, tone?: 'hard' | 'soft'): CellSpec => ({ text: integer(count), muted: !count, tone: count ? tone : undefined })
const countStat = (label: string, count: number, tone?: 'hard' | 'soft', meta?: string): DetailStat => ({
  label,
  value: integer(count),
  tone: count ? tone : undefined,
  tint: true,
  meta,
})

const findBuild = (number: number | null, ctx: MainCiContext) => (number ? ctx.builds.find((row) => row.number === number) : undefined)

/** A build number that opens the build's own sheet while the section still has it, else its Buildkite page. */
function buildCell(number: number | null, url: string, ctx: MainCiContext): CellSpec {
  if (!number) return text(null)
  const build = findBuild(number, ctx)
  return build ? { text: `#${number}`, mono: true, detail: () => mainBuildDetail(build, ctx) } : link(`#${number}`, url, true)
}

export function buildColumns(): ColumnSpec<MainBuild>[] {
  return [
    { id: 'build', label: 'Build', value: (row) => row.number, cell: (row) => link(`#${row.number}`, row.url, true), class: 'whitespace-nowrap' },
    {
      id: 'created',
      label: 'When',
      value: (row) => row.createdAt,
      cell: (row) => ({ text: shortDayTime(Date.parse(row.createdAt)), sub: row.nightly ? 'Nightly' : undefined, nowrap: true }),
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
    { id: 'hard', label: 'Hard', numeric: true, value: (row) => row.amd?.hard ?? -1, cell: (row) => countCell(row.amd?.hard ?? 0, 'hard'), class: 'hidden sm:table-cell' },
    { id: 'soft', label: 'Soft', numeric: true, value: (row) => row.amd?.soft ?? -1, cell: (row) => countCell(row.amd?.soft ?? 0, 'soft'), class: 'hidden sm:table-cell' },
    { id: 'retried', label: 'Retried', numeric: true, value: (row) => row.amd?.retried ?? -1, cell: (row) => countCell(row.amd?.retried ?? 0), class: 'hidden lg:table-cell' },
  ]
}

export const buildSearch = (row: MainBuild) => [row.number, row.headline, row.commit, buildOutcome(row), row.nightly ? 'nightly' : ''].join(' ')

/** Event columns; a group's own sheet drops the group, a build's own sheet the build and time. */
function eventColumns(ctx: MainCiContext, { group = true, build = true }: { group?: boolean; build?: boolean } = {}): ColumnSpec<TimelineEvent>[] {
  const columns: ColumnSpec<TimelineEvent>[] = [
    { id: 'event', label: 'Event', value: (row) => row.event, cell: (row) => badge(sentence(row.event), eventTone(row)), class: 'whitespace-nowrap' },
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => text(groupLabel(row.name)) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'build', label: 'Build', value: (row) => row.build ?? 0, cell: (row) => buildCell(row.build, row.buildUrl, ctx), class: 'whitespace-nowrap' },
    { id: 'at', label: 'When', value: (row) => row.at, cell: (row) => text(at(row.at)), class: 'hidden whitespace-nowrap md:table-cell' },
  ]
  return columns.filter((column) => (group || column.id !== 'group') && (build || (column.id !== 'build' && column.id !== 'at')))
}

const failingColumns: ColumnSpec<FailingGroup>[] = [
  { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ ...link(groupLabel(row.name), row.jobUrl), sub: row.queue || undefined }) },
  { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
  { id: 'result', label: 'Result', value: (row) => row.severity, cell: (row) => badge(TONE_LABEL[row.severity], row.severity), class: 'whitespace-nowrap' },
]

/** The evidence sheet of one main build: AMD outcome, its failing groups and incident events. */
export function mainBuildDetail(build: MainBuild, ctx: MainCiContext): Detail {
  const amd = build.amd
  const failingCount = amd ? amd.hard + amd.soft : 0
  const failing = failingCount ? failingGroups(build, ctx.record) : []
  const events = mainTimeline(ctx.record).filter((event) => event.build === build.number)
  const stats: DetailStat[] = amd
    ? [
        rateStat('AMD pass rate', passRate(amd), `${integer(amd.passed)} of ${integer(amd.passed + amd.soft + amd.hard)} jobs`),
        countStat('Hard failures', amd.hard, 'hard', 'jobs'),
        countStat('Soft failures', amd.soft, 'soft', 'jobs'),
        countStat('Retried', amd.retried, undefined, 'jobs Buildkite ran again'),
      ]
    : []
  const notes: DetailNote[] = []
  if (!amd) notes.push({ tone: 'neutral', text: 'This build recorded no AMD MI job counts; open it on Buildkite for its jobs.' })
  else if (failingCount && !failing?.length) notes.push({ tone: 'neutral', text: 'This snapshot does not list the failing groups of this build; open it on Buildkite for its jobs.' })
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
    notes,
    stats,
    fields,
    sources: [
      ...(build.url ? [{ label: 'Open Buildkite build', url: build.url }] : []),
      ...(build.commit ? [{ label: 'Open commit', url: commitUrl(build.commit) }] : []),
    ],
    tables: [
      ...(failing?.length
        ? [{ title: 'Failing AMD groups', meta: `${integer(failing.length)} in this build`, columns: failingColumns, rows: failing, pageSize: 10, searchText: (row: FailingGroup) => [row.name, row.hardware, row.queue].join(' ') }]
        : []),
      ...(events.length ? [{ title: 'Incident events at this build', meta: counted(events.length, 'event'), columns: eventColumns(ctx, { build: false }), rows: events, pageSize: 10 }] : []),
    ],
    empty: amd && !failingCount ? 'Every AMD job with a verdict passed in this build.' : undefined,
    footnote: build.nightly
      ? 'Main CI counts one final Buildkite attempt per AMD MI test group. The nightly evidence on Home and CI Analytics counts the jobs with parsed test results, so its totals for this build can differ.'
      : undefined,
    width: 'lg',
  }
}

/** Incident lifecycle events with their group, for the timeline table. */
export const timelineColumns = (ctx: MainCiContext) => eventColumns(ctx)

/** When an incident opened, badged "New" for its first day. */
function openedCell(row: MainIncident, now: number): CellSpec {
  if (!row.openedAt) return text(null)
  const fresh = isNewIncident(row, now)
  return { text: fresh ? `New · ${age(row.openedAt, now)}` : age(row.openedAt, now), tone: fresh ? 'info' : undefined, sub: at(row.openedAt) ?? undefined, nowrap: true }
}

/**
 * Incident columns, newest opening first. Severity is a column only when the rows mix severities
 * (pending soft failures, soft incidents); otherwise every row would carry the same badge.
 */
export function incidentColumns(ctx: MainCiContext, rows: MainIncident[], now: number): ColumnSpec<MainIncident>[] {
  const mixed = new Set(rows.map((row) => (row.pending ? 'pending' : row.severity))).size > 1
  const columns: ColumnSpec<MainIncident>[] = [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    ...(mixed
      ? [
          {
            id: 'severity',
            label: 'Severity',
            value: (row: MainIncident) => (row.pending ? 'pending' : row.severity),
            cell: (row: MainIncident) => badge(row.pending ? 'Pending soft' : TONE_LABEL[row.severity], row.severity),
            class: 'hidden whitespace-nowrap sm:table-cell',
          },
        ]
      : []),
    { id: 'opened', label: 'Opened', value: (row) => row.openedAt, cell: (row) => openedCell(row, now), class: 'whitespace-nowrap' },
    { id: 'since', label: 'First bad', value: (row) => row.bad.build ?? row.latestBuild ?? 0, cell: (row) => buildCell(row.bad.build ?? row.latestBuild, row.latestBuildUrl, ctx), class: 'hidden whitespace-nowrap sm:table-cell' },
    {
      id: 'range',
      label: 'Commit range',
      value: (row) => row.compareUrl || rangeText(row.rangeStatus).short,
      cell: (row) =>
        row.compareUrl ? link(`${row.good.commit.slice(0, 7)}…${row.bad.commit.slice(0, 7)}`, row.compareUrl, true) : { text: rangeText(row.rangeStatus).short, muted: true },
      class: 'hidden lg:table-cell',
    },
    { id: 'observed', label: 'Last seen', value: (row) => row.observedAt, cell: (row) => text(at(row.observedAt)), class: 'hidden whitespace-nowrap md:table-cell' },
  ]
  return columns
}

export const incidentSearch = (row: MainIncident) => [row.name, row.hardware, row.queue, row.severity, row.bad.build, row.good.build].join(' ')

/** One main-failure incident: the good and bad builds that bound it, its bisect command, and its events. */
export function incidentDetail(incident: MainIncident, ctx: MainCiContext, issue: { number: number; url: string } | null, now: number): Detail {
  const events = mainTimeline(ctx.record).filter((event) => event.groupId === incident.groupId)
  const bounds = [findBuild(incident.good.build, ctx), findBuild(incident.bad.build, ctx)].filter((build): build is MainBuild => !!build)
  const buildField = (label: string, number: number | null): DetailField => ({ label, value: number ? `#${number}` : null, href: findBuild(number, ctx)?.url || undefined, mono: true })
  const range = rangeText(incident.rangeStatus)
  return {
    id: `main-incident-${incident.groupId}`,
    title: groupLabel(incident.name),
    subtitle: [incident.pending ? 'Pending soft failure on main' : `${TONE_LABEL[incident.severity]} incident on main`, incident.hardware.toUpperCase(), incident.queue]
      .filter(Boolean)
      .join(' · '),
    notes: incident.pending ? [{ tone: 'soft', text: 'A soft failure is confirmed after it repeats in a second build; until then it is pending.' }] : [],
    fields: [
      { label: 'Severity', value: incident.pending ? 'Pending soft' : TONE_LABEL[incident.severity], tone: incident.severity },
      { label: 'Opened', value: incident.openedAt ? `${shortDate(incident.openedAt)} · ${age(incident.openedAt, now)}` : null },
      buildField('Last good build', incident.good.build),
      { label: 'Good commit', value: incident.good.commit ? shortSha(incident.good.commit) : null, href: incident.good.commit ? commitUrl(incident.good.commit) : undefined, mono: true },
      buildField('First bad build', incident.bad.build),
      { label: 'Bad commit', value: incident.bad.commit ? shortSha(incident.bad.commit) : null, href: incident.bad.commit ? commitUrl(incident.bad.commit) : undefined, mono: true },
      { label: 'Commit range', value: range.long },
      { label: 'Bisect', value: incident.bisectCommand || null, mono: true },
      { label: 'Latest failure', value: incident.observedAt ? shortDate(incident.observedAt) : null },
    ],
    sources: [
      ...(incident.jobUrl ? [{ label: 'Open failing job', url: incident.jobUrl }] : []),
      ...(incident.compareUrl ? [{ label: 'Compare commits', url: incident.compareUrl }] : []),
      ...(issue ? [{ label: `Managed issue #${issue.number}`, url: issue.url }] : []),
    ],
    tables: [
      ...(bounds.length
        ? [{ title: 'Last good and first bad builds', columns: buildColumns(), rows: bounds, onRowClick: (build: MainBuild) => mainBuildDetail(build, ctx), pageSize: false as const }]
        : []),
      ...(events.length ? [{ title: 'Incident events', meta: counted(events.length, 'event'), columns: eventColumns(ctx, { group: false }), rows: events, pageSize: 10 }] : []),
    ],
    footnote: 'An incident stays open until its group passes again on main, so it can outlive builds that did not run the group.',
    width: 'lg',
  }
}

/** The incidents that share one commit range, likely broken by one commit in it. */
export function suspectRangeDetail(range: SuspectRange, ctx: MainCiContext, issue: { number: number; url: string } | null, now: number): Detail {
  const bad = findBuild(range.bad.build, ctx)
  return {
    id: `main-range-${range.good.commit}-${range.bad.commit}`,
    title: `${range.good.commit.slice(0, 7)}…${range.bad.commit.slice(0, 7)}`,
    subtitle: `${counted(range.incidents.length, 'incident')} first failed at ${range.bad.build ? `#${range.bad.build}` : 'one commit'}`,
    description:
      'Every group below first failed at the bad commit. The good commit is the newest at which one of them still passed, so if one change broke them all, it lies in this range.',
    fields: [
      { label: 'Good commit', value: shortSha(range.good.commit), href: commitUrl(range.good.commit), mono: true },
      { label: 'Bad commit', value: shortSha(range.bad.commit), href: commitUrl(range.bad.commit), mono: true },
      { label: 'Opened', value: range.openedAt ? `${shortDate(range.openedAt)} · ${age(range.openedAt, now)}` : null },
      { label: 'Bisect', value: range.bisectCommand || null, mono: true },
    ],
    sources: [
      ...(range.compareUrl ? [{ label: 'Compare commits', url: range.compareUrl }] : []),
      ...(bad?.url ? [{ label: `Open build #${bad.number}`, url: bad.url }] : []),
    ],
    tables: [
      {
        title: 'Incidents in this range',
        meta: counted(range.incidents.length, 'group'),
        columns: incidentColumns(ctx, range.incidents, now).filter((column) => !['since', 'range'].includes(column.id)),
        rows: range.incidents,
        onRowClick: (row: MainIncident) => incidentDetail(row, ctx, issue, now),
        pageSize: false,
      },
    ],
    width: 'lg',
  }
}

/** The main builds behind one point of the pass-rate trend. */
export function trendPointDetail(point: TrendPoint, builds: MainBuild[], label: string, ctx: MainCiContext): Detail {
  return {
    id: `main-trend-${point.from}`,
    title: `Main builds · ${label}`,
    subtitle: `${counted(point.builds, 'build')} · ${integer(point.greenBuilds)} without a hard AMD failure`,
    stats: [
      rateStat(
        'AMD pass rate',
        point.passRate,
        `${integer(point.passed)} of ${integer(point.passed + point.soft + point.hard)} jobs${point.concentration ? ` · ${concentrationText(point.concentration)}` : ''}`,
        dayRateStatus(point),
      ),
      countStat('Passed jobs', point.passed),
      countStat('Hard failures', point.hard, 'hard', 'jobs'),
      countStat('Soft failures', point.soft, 'soft', 'jobs'),
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
  ref ? { ...buildCell(ref.number, ref.url, ctx), sub: at(ref.at) ?? undefined } : text(null)

const alertCell = (alerting: boolean): CellSpec => (alerting ? badge('Alerting', 'soft') : { text: 'Below threshold', muted: true })

export function flakyColumns(ctx: MainCiContext): ColumnSpec<FlakyGroup>[] {
  return [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'alert', label: 'Alert', value: (row) => (row.alerting ? 1 : 0), cell: (row) => alertCell(row.alerting), class: 'hidden whitespace-nowrap sm:table-cell' },
    { id: 'flips', label: 'Flips', numeric: true, value: (row) => row.flips, cell: (row) => ({ text: integer(row.flips), sub: `prev ${integer(row.previousFlips)}` }) },
    { id: 'flips30', label: '30 days', numeric: true, value: (row) => row.flips30d, cell: (row) => ({ text: integer(row.flips30d), sub: `${integer(row.runs30d)} runs` }), class: 'hidden md:table-cell' },
    { id: 'rate', label: 'Pass rate', numeric: true, value: (row) => row.passRate ?? -1, cell: (row) => rateCell(row.passRate, `30d ${rateText(row.passRate30d)}`), class: 'hidden sm:table-cell' },
    { id: 'last', label: 'Last failure', value: (row) => row.lastFailure?.number ?? 0, cell: (row) => lastBuildCell(row.lastFailure, ctx), class: 'hidden whitespace-nowrap lg:table-cell' },
  ]
}

export function retryColumns(ctx: MainCiContext): ColumnSpec<RetryGroup>[] {
  return [
    { id: 'group', label: 'Test group', value: (row) => groupLabel(row.name), cell: (row) => ({ text: groupLabel(row.name), sub: row.queue || undefined }) },
    { id: 'hardware', label: 'Hardware', value: (row) => row.hardware, cell: (row) => hardwareCell(row.hardware, row.queue), class: 'hidden sm:table-cell' },
    { id: 'alert', label: 'Alert', value: (row) => (row.alerting ? 1 : 0), cell: (row) => alertCell(row.alerting), class: 'hidden whitespace-nowrap sm:table-cell' },
    { id: 'recovered', label: 'Recoveries', numeric: true, value: (row) => row.recovered, cell: (row) => ({ text: integer(row.recovered), sub: `prev ${integer(row.previousRecovered)}` }) },
    { id: 'recovered30', label: '30 days', numeric: true, value: (row) => row.recovered30d, cell: (row) => ({ text: integer(row.recovered30d), sub: `${integer(row.runs30d)} runs` }), class: 'hidden md:table-cell' },
    { id: 'last', label: 'Last recovery', value: (row) => row.lastRecovery?.number ?? 0, cell: (row) => lastBuildCell(row.lastRecovery, ctx), class: 'hidden whitespace-nowrap lg:table-cell' },
  ]
}

export const groupSearch = (row: { name: string; hardware: string; queue: string }) => [row.name, row.hardware, row.queue].join(' ')

/** One flaky or retry-recovering group: its window statistics and the build behind its latest event. */
export function groupStatsDetail(row: FlakyGroup | RetryGroup, ctx: MainCiContext, thresholds: FlakinessThresholds | null): Detail {
  const flaky = 'flips' in row
  const window = thresholds ? `${thresholds.windowDays} days` : 'window'
  const stats: DetailStat[] = flaky
    ? [
        { label: `Flips (${window})`, value: integer(row.flips), meta: `previous ${window}: ${integer(row.previousFlips)}` },
        { label: 'Flips (30 days)', value: integer(row.flips30d), meta: `${integer(row.runs30d)} runs` },
        rateStat(`Pass rate (${window})`, row.passRate),
        { label: 'Failures (30 days)', value: `${integer(row.hard30d)} hard · ${integer(row.soft30d)} soft` },
      ]
    : [
        { label: `Recoveries (${window})`, value: integer(row.recovered), meta: `previous ${window}: ${integer(row.previousRecovered)}` },
        { label: 'Recoveries (30 days)', value: integer(row.recovered30d), meta: `${integer(row.runs30d)} runs` },
      ]
  const criterion = thresholds ? (flaky ? `${thresholds.minFlips} or more flips` : `${thresholds.minRecoveries} or more recoveries`) : 'the watcher threshold'
  const last = flaky ? row.lastFailure : row.lastRecovery
  const lastBuild = last ? findBuild(last.number, ctx) : undefined
  return {
    id: `main-group-${flaky ? 'flaky' : 'retry'}-${row.groupId}`,
    title: groupLabel(row.name),
    subtitle: [flaky ? 'Outcome flips on main' : 'Passes only after a retry on main', row.hardware.toUpperCase(), row.queue].filter(Boolean).join(' · '),
    description: flaky
      ? 'A flip is a change between pass and fail in consecutive main builds that ran the group; a pass after a retried hard failure counts as a pass.'
      : 'A recovery is a hard-failed attempt that Buildkite retried and that then passed in the same build.',
    notes: row.alerting ? [{ tone: 'soft', title: 'Alerting', text: `This group has ${criterion} in the last ${window}.` }] : [],
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
