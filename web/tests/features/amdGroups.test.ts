import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'
import { exactJobDetail, logicalGroupDetail } from '@/features/amd-groups/details'
import GroupHistory from '@/features/amd-groups/GroupHistory.vue'
import { installLayoutStubs, settle, text } from '../components/support'

installLayoutStubs()

const job = (build: number, state: string) => ({
  build_number: build,
  state,
  observed_at: `2026-10-0${build - 30000}T09:00:00Z`,
  url: `https://buildkite.com/vllm/ci/builds/${build}/steps/canvas?jid=j${build}`,
  queue: 'amd_mi300_1',
})

describe('AMD group drill-downs', () => {
  it('builds the exact job evidence with its outcome strip and failure table', () => {
    const row = { id: 'g1', display_name: 'Kernels', hardware_variant: 'mi300_1', queue: 'amd_mi300_1', latest_build_number: 30003, latest_state: 'failed', passed: 2, hard_failed: 1, runs: 3, observations: [job(30001, 'passed'), job(30003, 'failed'), job(30002, 'passed')] }
    const detail = exactJobDetail(row, 30003)
    expect(detail.stats![0]).toMatchObject({ value: 'Hard fail', tone: 'hard' })
    expect(detail.sections![0]!.props!.observations).toHaveLength(3)
    expect(detail.tables![0]!.rows).toHaveLength(1)
  })

  it('links a job variant to Buildkite when the catalog has no exact group', () => {
    const detail = logicalGroupDetail(
      { label: 'Kernels', state: 'partial', job_variants: [{ id: 'missing', display_name: 'Kernels MI355', job_url: 'https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=x' }] },
      { amdHealth: null, latestBuild: 1 },
    )
    const cell = detail.tables![0]!.columns[0]!.cell!(detail.tables![0]!.rows[0])
    expect(cell).toMatchObject({ text: 'Kernels MI355', href: 'https://buildkite.com/vllm/ci/builds/1/steps/canvas?jid=x' })
    expect(cell.detail).toBeUndefined()
  })

  it('shows retained ledger evidence for a group instead of the load action', async () => {
    const reliability = {
      source_pipeline: 'ci',
      job_scope: 'amd_gpu',
      hardware_scope: 'amd_mi_gpu',
      group_catalog: [{ name: 'Kernels', hardware: 'mi300', queue: 'amd_mi300_1', observations: [job(30001, 'passed'), job(30002, 'failed')] }],
    }
    const wrapper = mount(GroupHistory, { props: { group: { name: 'Kernels', area: 'Kernels' }, ops: { reliability } }, global: { stubs: { LineChart: true } } })
    await settle()
    expect(text(wrapper.element)).toContain('mixed-outcome candidate')
    expect(wrapper.findAll('[data-testid=outcome-strip] a')).toHaveLength(2)
    expect(text(wrapper.element)).not.toContain('Load 30-day run history')
    wrapper.unmount()
  })
})
