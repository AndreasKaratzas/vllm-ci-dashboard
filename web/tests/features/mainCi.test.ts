import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { mainBuilds, mainIncidents } from '@/domain/mainCi'
import BuildStrip from '@/features/main-ci/BuildStrip.vue'
import { incidentDetail, mainBuildDetail, trendPointDetail } from '@/features/main-ci/details'
import FailingGroups from '@/features/main-ci/FailingGroups.vue'
import { installLayoutStubs, settle } from '../components/support'

installLayoutStubs()

const counts = (passed: number, hard = 0, soft = 0) => ({ passed, soft, hard, unknown: 0, not_run: 0, retried: hard })
const record = {
  builds: [
    { number: 3, created_at: '2026-10-20T03:00:00Z', commit: 'c'.repeat(40), headline: 'Fix', state: 'failed', amd: counts(40, 2), url: 'https://buildkite.com/vllm/ci/builds/3' },
    { number: 2, created_at: '2026-10-20T02:00:00Z', commit: 'b'.repeat(40), headline: 'Feature', state: 'passed', amd: counts(40), url: 'https://buildkite.com/vllm/ci/builds/2' },
    { number: 1, created_at: '2026-10-20T01:00:00Z', commit: 'a'.repeat(40), headline: 'Old', state: 'passed', amd: null, url: 'https://buildkite.com/vllm/ci/builds/1' },
  ],
  incidents: {
    available: true,
    issue: { number: 737, url: 'https://github.com/AndreasKaratzas/vllm-ci-dashboard/issues/737' },
    active: [{ group_id: 'g1', name: ':amd: (MI355) Kernels', hardware: 'mi355', severity: 'hard', bad_build_number: 3, good_build_number: 2, compare_url: 'https://github.com/x/compare/a...b', job_url: 'https://buildkite.com/vllm/ci/builds/3/steps/canvas?jid=j' }],
    pending_soft: [],
  },
  timeline: [
    { event: 'opened', group_id: 'g1', name: ':amd: (MI355) Kernels', severity: 'hard', build_number: 3 },
    { event: 'resolved', group_id: 'g9', name: 'Other', severity: 'hard', build_number: 2 },
  ],
}
const builds = mainBuilds(record)
const ctx = { record, builds }

describe('main CI sheets', () => {
  it('lists a failing build’s groups from the ledger and its incident events', () => {
    const detail = mainBuildDetail(builds[0]!, ctx)
    expect(detail.title).toBe('Main build #3')
    expect(detail.sources!.map((source) => source.label)).toEqual(['Open Buildkite build', 'Open commit'])
    expect(detail.sections![0]).toMatchObject({ title: 'Failing AMD groups', component: FailingGroups, props: { build: builds[0] } })
    expect(detail.tables![0]!.rows).toHaveLength(1)
    expect(detail.tables![0]!.columns.map((column) => column.id)).toEqual(['event', 'group', 'hardware'])
  })

  it('says a passing build passed and a build without counts has none', () => {
    expect(mainBuildDetail(builds[1]!, ctx)).toMatchObject({ sections: [], empty: 'Every AMD job with a verdict passed in this build.' })
    expect(mainBuildDetail(builds[2]!, ctx).notes![0]!.text).toContain('no AMD MI job counts')
  })

  it('links an incident to its commits, job and managed issue, and its first bad build to that build’s sheet', () => {
    const incidents = mainIncidents(record)
    const detail = incidentDetail(incidents.active[0]!, ctx, incidents.issue)
    expect(detail.sources!.map((source) => source.label)).toEqual(['Open failing job', 'Compare commits', 'Managed issue #737'])
    expect(detail.tables![0]!.columns.map((column) => column.id)).toEqual(['event', 'hardware', 'build', 'at'])
    const cell = detail.tables![0]!.columns.find((column) => column.id === 'build')!.cell!(detail.tables![0]!.rows[0])
    expect(cell.detail!().title).toBe('Main build #3')
  })

  it('opens the builds of one trend point', () => {
    const point = { from: Date.parse('2026-10-20T02:00:00Z'), to: Date.parse('2026-10-20T04:00:00Z'), builds: 2, amdBuilds: 2, greenBuilds: 1, passed: 80, soft: 0, hard: 2, passRate: 97.6 }
    const detail = trendPointDetail(point, builds.slice(0, 2), 'Oct 20', ctx)
    expect(detail.stats![0]).toMatchObject({ value: '97.6%', tone: 'soft' })
    expect(detail.tables![0]!.onRowClick!(builds[0]!)!.title).toBe('Main build #3')
  })
})

describe('BuildStrip', () => {
  it('draws the newest build last, colors by outcome and opens a build from the keyboard', async () => {
    const wrapper = mount(BuildStrip, { props: { builds }, attachTo: document.body })
    await settle()
    const tiles = wrapper.findAll('[data-slot=main-build-tile]')
    expect(tiles.map((tile) => tile.attributes('data-tone'))).toEqual(['neutral', 'pass', 'hard'])
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
