import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import type { Rec } from '@/domain/format'
import { dayRateStatus, lastDayPoint, mainBuilds, mainCiSummary, mainIncidents, suspectRanges } from '@/domain/mainCi'
import BuildStrip from '@/features/main-ci/BuildStrip.vue'
import {
  buildColumns,
  flakyColumns,
  groupStatsDetail,
  incidentColumns,
  incidentDetail,
  mainBuildDetail,
  retryColumns,
  suspectRangeDetail,
  trendPointDetail,
  type MainCiContext,
} from '@/features/main-ci/details'
import { flakyGroups, retryGroups } from '@/domain/mainCi'
import AreaChart from '@/components/charts/AreaChart.vue'
import MainCiKpis from '@/features/main-ci/MainCiKpis.vue'
import MainCiTrendCard from '@/features/main-ci/MainCiTrendCard.vue'
import { installLayoutStubs, settle, testRouter } from '../components/support'

installLayoutStubs()

const NOW = Date.parse('2026-10-20T06:00:00Z')
const counts = (passed: number, hard = 0, soft = 0) => ({ passed, soft, hard, unknown: 0, not_run: 0, retried: hard })
const incident = (group_id: string, name: string, extra = {}) => ({
  group_id,
  name,
  hardware: 'mi355',
  severity: 'hard',
  bad_build_number: 3,
  good_build_number: 2,
  good_commit: 'b'.repeat(40),
  bad_commit: 'c'.repeat(40),
  commit_range_status: 'candidate',
  compare_url: 'https://github.com/x/compare/b...c',
  job_url: 'https://buildkite.com/vllm/ci/builds/3/steps/canvas?jid=j',
  ...extra,
})
const record = {
  group_catalog: { g1: { name: ':amd: (MI355) Kernels', hardware: 'mi355', queue: 'amd_mi355_1' }, g4: { name: 'Soft one', hardware: 'mi300', queue: 'amd_mi300_1' } },
  builds: [
    {
      number: 3,
      created_at: '2026-10-20T03:00:00Z',
      commit: 'c'.repeat(40),
      headline: 'Fix',
      state: 'failed',
      amd: counts(40, 1, 1),
      url: 'https://buildkite.com/vllm/ci/builds/3',
      failing: [
        { group_id: 'g1', severity: 'hard', job_url: 'https://buildkite.com/vllm/ci/builds/3/steps/canvas?jid=j' },
        { group_id: 'g4', severity: 'soft' },
      ],
    },
    { number: 2, created_at: '2026-10-20T02:00:00Z', commit: 'b'.repeat(40), headline: 'Feature', state: 'passed', amd: counts(40), url: 'https://buildkite.com/vllm/ci/builds/2', failing: [] },
    { number: 1, created_at: '2026-10-19T01:00:00Z', commit: 'a'.repeat(40), headline: 'Nightly', nightly: true, state: 'failed', amd: counts(40, 2), url: 'https://buildkite.com/vllm/ci/builds/1' },
  ],
  incidents: {
    available: true,
    issue: { number: 737, url: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/issues/737' },
    active: [incident('g1', ':amd: (MI355) Kernels'), incident('g2', ':amd: (MI355) Attention')],
    pending_soft: [incident('g3', 'Pending one', { severity: 'soft', bad_build_number: 2, compare_url: '', commit_range_status: 'missing_good', good_commit: '' })],
  },
  timeline: [
    { event: 'opened', group_id: 'g1', name: ':amd: (MI355) Kernels', severity: 'hard', build_number: 3, at: '2026-10-20T03:00:00Z' },
    { event: 'resolved', group_id: 'g9', name: 'Other', severity: 'hard', build_number: 2 },
  ],
}
const builds = mainBuilds(record)
const ctx: MainCiContext = { record, builds }
const incidents = mainIncidents(record)

describe('main CI sheets', () => {
  it('lists a failing build’s groups by name, hard first, and its incident events', () => {
    const detail = mainBuildDetail(builds[0]!, ctx)
    expect(detail.title).toBe('Main build #3')
    expect(detail.sources!.map((source) => source.label)).toEqual(['Open Buildkite build', 'Open commit'])
    const [failing, events] = detail.tables!
    expect(failing).toMatchObject({ title: 'Failing AMD groups', meta: '2 in this build' })
    expect(failing!.rows.map((row: { name: string; severity: string }) => [row.name, row.severity])).toEqual([
      [':amd: (MI355) Kernels', 'hard'],
      ['Soft one', 'soft'],
    ])
    const name = failing!.columns[0]!.cell!(failing!.rows[0])
    expect(name).toMatchObject({ text: '(MI355) Kernels', href: 'https://buildkite.com/vllm/ci/builds/3/steps/canvas?jid=j' })
    expect(events).toMatchObject({ meta: '1 event' })
    expect(events!.columns.map((column) => column.id)).toEqual(['event', 'group', 'hardware'])
    // Failure counts are tinted, not badged; the rate names its band.
    expect(detail.stats!.map((stat) => [stat.label, stat.tone, stat.toneLabel, stat.tint])).toEqual([
      ['AMD pass rate', 'soft', 'Below 99%', undefined],
      ['Hard failures', 'hard', undefined, true],
      ['Soft failures', 'soft', undefined, true],
      ['Retried', undefined, undefined, true],
    ])
    expect(detail.footnote).toBeUndefined()
  })

  it('says a passing build passed, and a build whose failing groups are not listed says so', () => {
    const passed = mainBuildDetail(builds[1]!, ctx)
    expect(passed.empty).toBe('Every AMD job with a verdict passed in this build.')
    expect(passed.tables!.map((table) => table.title)).toEqual(['Incident events at this build'])
    const nightly = mainBuildDetail(builds[2]!, ctx)
    expect(nightly.notes![0]!.text).toContain('does not list the failing groups')
    expect(nightly.footnote).toContain('parsed test results')
    expect(mainBuildDetail({ ...builds[1]!, amd: null }, ctx).notes![0]!.text).toContain('no AMD MI job counts')
  })

  it('links an incident to its bounding builds, commits, job and managed issue', () => {
    const detail = incidentDetail(incidents.active.find((row) => row.groupId === 'g1')!, ctx, incidents.issue, NOW)
    expect(detail.sources!.map((source) => source.label)).toEqual(['Open failing job', 'Compare commits', 'Managed issue #737'])
    const fields = Object.fromEntries(detail.fields!.map((field) => [field.label, field]))
    expect(fields['Last good build']).toMatchObject({ value: '#2', href: 'https://buildkite.com/vllm/ci/builds/2' })
    expect(fields['First bad build']).toMatchObject({ value: '#3', href: 'https://buildkite.com/vllm/ci/builds/3' })
    expect(fields['Commit range']!.value).toContain('Bisectable')
    expect(fields['Opened']!.value).toContain('3h ago')
    const [bounds, events] = detail.tables!
    expect(bounds!.rows.map((build: { number: number }) => build.number)).toEqual([2, 3])
    expect(bounds!.onRowClick!(bounds!.rows[1])!.title).toBe('Main build #3')
    expect(events).toMatchObject({ meta: '1 event' })
    const cell = events!.columns.find((column) => column.id === 'build')!.cell!(events!.rows[0])
    expect(cell.detail!().title).toBe('Main build #3')
  })

  it('shows severity only when it varies, and marks incidents opened in the last day as new', () => {
    const active = incidentColumns(ctx, incidents.active, NOW)
    expect(active.map((column) => column.id)).toEqual(['group', 'hardware', 'opened', 'since', 'range', 'observed'])
    expect(incidentColumns(ctx, [...incidents.active, ...incidents.pending], NOW).map((column) => column.id)).toContain('severity')
    const opened = active.find((column) => column.id === 'opened')!
    const g1 = incidents.active.find((row) => row.groupId === 'g1')!
    expect(opened.cell!(g1)).toMatchObject({ text: 'New · 3h ago', tone: 'info' })
    expect(opened.cell!({ ...g1, openedAt: '2026-10-18T00:00:00Z' })).toMatchObject({ text: '2d ago', tone: undefined })
    const since = active.find((column) => column.id === 'since')!.cell!(g1)
    expect(since.detail!().title).toBe('Main build #3')
    const range = active.find((column) => column.id === 'range')!
    expect(range.cell!(g1)).toMatchObject({ text: 'bbbbbbb…ccccccc', href: 'https://github.com/x/compare/b...c' })
    expect(range.cell!(incidents.pending[0]!)).toMatchObject({ text: 'No good build', muted: true })
  })

  it('opens the incidents that share a commit range', () => {
    const [range] = suspectRanges(incidents.active)
    const detail = suspectRangeDetail(range!, ctx, incidents.issue, NOW)
    expect(detail.subtitle).toBe('2 incidents first failed at #3')
    expect(detail.sources!.map((source) => source.label)).toEqual(['Compare commits', 'Open build #3'])
    expect(detail.tables![0]!.columns.map((column) => column.id)).toEqual(['group', 'hardware', 'opened', 'observed'])
    expect(detail.tables![0]!.onRowClick!(range!.incidents[0])!.subtitle).toContain('incident on main')
  })

  it('opens the builds of one trend point', () => {
    const point = { from: Date.parse('2026-10-20T02:00:00Z'), to: Date.parse('2026-10-20T04:00:00Z'), builds: 2, amdBuilds: 2, greenBuilds: 1, passed: 80, soft: 0, hard: 2, passRate: 97.6 }
    const detail = trendPointDetail(point, builds.slice(0, 2), 'Oct 20', ctx)
    expect(detail.stats![0]).toMatchObject({ value: '97.6%', tone: 'soft', toneLabel: 'Below 99%' })
    expect(detail.tables![0]!.onRowClick!(builds[0]!)!.title).toBe('Main build #3')
  })

  it('keeps phones on the build, its time and its result', () => {
    const phone = buildColumns().filter((column) => !column.class?.includes('hidden'))
    expect(phone.map((column) => column.id)).toEqual(['build', 'created', 'outcome'])
  })
})

describe('flaky and retry sheets', () => {
  const thresholds = { windowDays: 7, minFlips: 3, minRecoveries: 3 }
  const flakyRecord = {
    top_flaky: [{ group_id: 'g', name: 'Mixed', hardware: 'mi300', alerting: false, flips_window: 1, pass_rate_window: 60, pass_rate_30d: 99.5, last_failure: { build_number: 3, at: '2026-10-20T03:00:00Z', url: 'u3' } }],
    retry_hotspots: [{ group_id: 'g', name: 'Mixed', hardware: 'mi300', alerting: true, recovered_window: 3, last_recovery: { build_number: 9, at: '2026-10-20T03:00:00Z', url: 'u9' } }],
  }
  const [flaky] = flakyGroups(flakyRecord)
  const [retry] = retryGroups(flakyRecord)

  it('names the criterion a group alerts on', () => {
    expect(groupStatsDetail(flaky!, ctx, thresholds).notes).toEqual([])
    const detail = groupStatsDetail(retry!, ctx, thresholds)
    expect(detail.notes![0]!.text).toBe('This group has 3 or more recoveries in the last 7 days.')
    expect(detail.tables).toEqual([])
  })

  it('opens the build of the last failure while the section still has it', () => {
    const detail = groupStatsDetail(flaky!, ctx, thresholds)
    expect(detail.stats!.find((stat) => stat.label === 'Pass rate (7 days)')).toMatchObject({ value: '60.0%', tone: 'hard', toneLabel: 'Below 95%' })
    expect(detail.tables![0]!.onRowClick!(builds[0]!)!.title).toBe('Main build #3')
  })

  it('tones pass-rate cells by band and badges only alerting rows', () => {
    const columns = Object.fromEntries(flakyColumns(ctx).map((column) => [column.id, column]))
    expect(columns.rate!.cell!(flaky!)).toMatchObject({ text: '60.0%', tone: 'hard', sub: '30d 99.5%' })
    expect(columns.alert!.cell!(flaky!)).toMatchObject({ text: 'Below threshold', muted: true })
    const retryAlert = retryColumns(ctx).find((column) => column.id === 'alert')!
    expect(retryAlert.cell!(retry!)).toMatchObject({ text: 'Alerting', tone: 'soft' })
    expect(columns.last!.cell!(flaky!)).toMatchObject({ text: '#3', sub: 'Oct 20, 03:00' })
  })
})

describe('BuildStrip', () => {
  it('draws the newest build last, colors by outcome and opens a build from the keyboard', async () => {
    const wrapper = mount(BuildStrip, { props: { builds }, attachTo: document.body })
    await settle()
    const tiles = wrapper.findAll('[data-slot=main-build-tile]')
    expect(tiles.map((tile) => tile.attributes('data-tone'))).toEqual(['hard', 'pass', 'hard'])
    expect(tiles.map((tile) => tile.attributes('tabindex'))).toEqual(['-1', '-1', '0'])
    expect(wrapper.find('[data-slot=main-build-readout]').text()).toContain('#3')
    await tiles[2]!.trigger('keydown', { key: 'Home' })
    await settle()
    expect(document.activeElement).toBe(tiles[0]!.element)
    await tiles[0]!.trigger('click')
    expect(wrapper.emitted('select')![0]![0]).toMatchObject({ number: 1 })
    wrapper.unmount()
  })
})

describe('MainCiTrendCard', () => {
  it('treats a whole six-hour block as one step, so a partial first block draws no gaps', async () => {
    // 03:00 to 23:00 UTC: blocks 03-05 (partial), 06-11, 12-17, 18-23.
    const hourly = Array.from({ length: 21 }, (_, index) => ({
      t: new Date(Date.UTC(2026, 9, 20, 3 + index)).toISOString(),
      builds: 1,
      amd_builds: 1,
      green_builds: 1,
      passed: 10,
      soft: 0,
      hard: 0,
    }))
    const router = await testRouter('/?window=7d')
    const wrapper = mount(MainCiTrendCard, { props: { record: { series: { hourly } } }, global: { plugins: [router], stubs: { AreaChart: true } } })
    await settle()
    const chart = wrapper.findComponent(AreaChart).props() as { rows: unknown[]; maxGap: number }
    expect(chart.rows).toHaveLength(4)
    expect(chart.maxGap).toBe(1.5 * 6 * 3_600_000)
    wrapper.unmount()
  })
})

describe('MainCiKpis', () => {
  const day = { builds: 67, amd_builds: 67, green_builds: 47, passed: 4444, soft: 0, hard: 368, pass_rate: 92.35 }
  const rateCard = async (lastDay: Rec) => {
    const summary = mainCiSummary({ available: true, summary: { last_24h: lastDay } })!
    const wrapper = mount(MainCiKpis, { props: { summary, record: null, builds: [], actions: {} } })
    await settle()
    const text = wrapper.get('[data-testid="kpi-main-rate"]').text()
    wrapper.unmount()
    return text
  }

  it('keeps a sub-95% day red unless its hard jobs are concentrated in a few builds while main is green', async () => {
    expect(await rateCard(day)).toContain('Below 95%')
    const concentrated = await rateCard({ ...day, hard_concentration: { builds: [96041, 96045, 96047], hard: 326, pass_rate_without: 99.03 } })
    expect(concentrated).toContain('Concentrated in 3 builds')
    expect(concentrated).not.toContain('Below 95%')
    expect(concentrated).toContain('326 in #96041, #96045, #96047 · 99.0% without them')
  })

  it('gives every 24h rate card, CI Health included, the same concentration-aware tone', () => {
    const status = (lastDay: Rec) => dayRateStatus(mainCiSummary({ available: true, summary: { last_24h: lastDay } })!.last24h)
    expect(status(day)).toEqual({ tone: 'hard', label: 'Below 95%' })
    expect(status({ ...day, hard_concentration: { builds: [96041, 96045, 96047], hard: 326 } })).toEqual({ tone: 'soft', label: 'Concentrated in 3 builds' })
    expect(status({ ...day, pass_rate: 99.1, hard_concentration: { builds: [96041], hard: 20 } })).toBeUndefined()
  })

  it('opens the last 24 hours with the same tone and concentrated builds as the card', () => {
    const lastDay = { ...day, hard_concentration: { builds: [96041, 96045, 96047], hard: 326, pass_rate_without: 99.03 } }
    const summary = mainCiSummary({ available: true, source_generated_at: '2026-10-20T12:00:00Z', summary: { last_24h: lastDay } })!
    const stat = trendPointDetail(lastDayPoint(summary)!, [], 'last 24 hours', ctx).stats!.find((row) => row.label === 'AMD pass rate')
    expect(stat).toMatchObject({ tone: 'soft', toneLabel: 'Concentrated in 3 builds' })
    expect(stat?.meta).toContain('326 in #96041, #96045, #96047 · 99.0% without them')
  })
})
