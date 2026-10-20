import { describe, expect, it } from 'vitest'
import { amdCompleteLabel, amdNightlyPresentation, nightlyState } from '@/domain/nightly'
import { buildEvidenceDetail } from '@/features/nightly/buildEvidence'

const NOW = Date.parse('2026-10-20T15:00:00Z')
const amdComplete = (extra = {}) => ({
  number: 96078,
  state: 'failed',
  source_state: 'failing',
  amd_complete: true,
  non_amd_jobs_active: 2,
  has_test_results: true,
  total_groups: 310,
  failed_groups: [{ name: 'mi355_1: Kernels', state: 'failed' }],
  soft_failed_groups: [],
  ...extra,
})

describe('AMD-complete nightlies', () => {
  it('labels final AMD results while non-AMD jobs keep the build running', () => {
    expect(amdCompleteLabel(amdComplete())).toBe('AMD jobs complete · build still running (2 non-AMD jobs)')
    expect(amdCompleteLabel(amdComplete({ non_amd_jobs_active: 1 }))).toBe('AMD jobs complete · build still running (1 non-AMD job)')
    expect(amdCompleteLabel(amdComplete({ non_amd_jobs_active: 0, source_state: 'running' }))).toBe(
      'AMD jobs complete · build still running',
    )
    expect(amdCompleteLabel(amdComplete({ source_state: 'canceling' }))).toBe(
      'AMD jobs complete · build canceling (2 non-AMD jobs)',
    )
  })

  it('has no label once Buildkite finishes or before the AMD jobs are final', () => {
    expect(amdCompleteLabel(amdComplete({ source_state: 'failed' }))).toBeNull()
    expect(amdCompleteLabel(amdComplete({ amd_complete: false }))).toBeNull()
    expect(amdCompleteLabel({ state: 'failing', source_state: 'failing' })).toBeNull()
  })

  it('presents the AMD outcome and says the build is still running', () => {
    const presentation = amdNightlyPresentation(amdComplete(), {}, '2026-10-20T14:30:00Z', NOW)
    expect(presentation).toMatchObject({ hasSignal: true, label: 'Hard failures', tone: 'hard', incidentCount: 1 })
    expect(presentation.meta).toBe(
      '#96078 - 309 pass - 0 soft - 1 hard; Movement unavailable; AMD jobs complete · build still running (2 non-AMD jobs)',
    )
  })

  it('explains a withdrawn signal while a manual AMD retry runs', () => {
    const retrying = { number: 96078, state: 'failing', source_state: 'failing', has_test_results: false, active_retry: true }
    expect(amdNightlyPresentation(retrying, { latest_build_number: 96001 }, '2026-10-20T14:30:00Z', NOW)).toMatchObject({
      hasSignal: false,
      label: 'AMD retry running',
      tone: 'info',
      meta: '#96078 - an AMD job was retried; results return once the retry settles; latest test signal #96001',
    })
    expect(nightlyState(retrying)?.label).toBe('AMD retry running')
  })

  it('keeps the AMD outcome apart from the Buildkite state on the evidence sheet', () => {
    const detail = buildEvidenceDetail(amdComplete({ url: 'https://buildkite.com/vllm/ci/builds/96078' }))
    const fields = Object.fromEntries((detail.fields ?? []).map((field) => [field.label, field.value]))
    expect(fields).toMatchObject({ 'AMD outcome': 'Failed', 'Buildkite state': 'Failing' })
    expect(detail.notes?.[0]).toMatchObject({ tone: 'info', title: 'AMD results are final' })
    expect(detail.notes?.[0]?.text).toContain('AMD jobs complete · build still running (2 non-AMD jobs).')
    const finished = buildEvidenceDetail(amdComplete({ state: 'passed', source_state: 'passed', amd_complete: false }))
    expect(finished.fields?.find((field) => field.label === 'AMD outcome')?.value).toBeNull()
    expect(finished.notes?.some((note) => note.title === 'AMD results are final')).toBe(false)
  })
})
