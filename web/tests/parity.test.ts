// Differential parity: the v2 domain modules must reproduce the classic
// renderer's (docs/assets/js/ops-v2.js) decisions on the same inputs.
import { beforeAll, describe, expect, it } from 'vitest'
import { populationSemantics, observedCountLabel, toneForState, type Rec } from '@/domain/format'
import { attentionLabel, currentAmdHealth, currentTestGroupParity } from '@/domain/health'
import {
  amdNightlyPresentation,
  ciHealthPublicationRetentionMessage,
  latestAmd,
  nightlyBuildEvidence,
  nightlyFailureCount,
  nightlyFailureMovement,
} from '@/domain/nightly'
import { exactPipelineBuildUrl, exactPipelineEvidenceUrl } from '@/domain/urls'
import { classicParity, loadClassic } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { expectGoldenFile } from './support/golden'
import { rng } from './support/rng'

const EXPORTED = [
  'latestAmd',
  'amdNightlyPresentation',
  'nightlyFailureMovement',
  'nightlyFailureCount',
  'nightlyBuildEvidence',
  'currentAmdHealth',
  'currentTestGroupParity',
  'ciHealthPublicationRetentionMessage',
  'attentionLabel',
  'exactPipelineBuildUrl',
  'exactPipelineEvidenceUrl',
  'populationSemantics',
  'observedCountLabel',
]

const shell: Rec = readFixture<Rec>('operations_v2_manifest.json').shell
pinClock(shell.generated_at)
const parity = classicParity('nightly')
let classic: ReturnType<typeof loadClassic>

beforeAll(() => {
  classic = loadClassic(EXPORTED)
})

const STATES = ['passed', 'failed', 'running', 'scheduled', 'canceled', 'blocked', 'failing', 'unknown', '']

function mutateBuild(build: Rec, rand: () => number): Rec {
  const b: Rec = structuredClone(build)
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  if (rand() < 0.4) b.state = pick(STATES)
  if (rand() < 0.2) b.has_test_results = false
  if (rand() < 0.2) b.test_jobs_blocked = Math.floor(rand() * 40)
  if (rand() < 0.2) delete b.failure_movement
  if (rand() < 0.15 && b.failure_movement) b.failure_movement.policy_id = 'other'
  if (rand() < 0.2 && b.failure_movement) {
    for (const key of ['new', 'recurring', 'fixed']) {
      if (Array.isArray(b.failure_movement[key]) && rand() < 0.5) b.failure_movement[key] = b.failure_movement[key].slice(1)
    }
  }
  if (rand() < 0.2) {
    b.transitions = {
      policy_id: 'confirmed-incidents-v1',
      preceding_build_number: rand() < 0.8 ? 1 : 0,
      new: (b.failed_groups || []).slice(0, 3).map((row: Rec, i: number) => ({
        ...row,
        current_severity: i % 2 ? 'soft' : 'hard',
        soft_streak: i,
        transition_change: i % 3 ? 'confirmed' : 'new',
      })),
      recurring: (b.soft_failed_groups || []).slice(0, 2),
      fixed: [],
      pending_soft: [{ name: 'p', transition_change: 'pending_started', state: 'soft' }],
    }
    delete b.failure_movement
  }
  if (rand() < 0.15) b.number = undefined
  return b
}

// Synthetic nightlies whose movement matches their incidents, so every
// comparison branch (improved, more failures, net even, ...) is exercised.
function syntheticBuild(rand: () => number, index: number): { build: Rec; summary: Rec } {
  const n = (max: number) => Math.floor(rand() * (max + 1))
  const row = (kind: string, j: number, state: string): Rec => {
    const r: Rec = {
      name: `${kind}-${j}`,
      state: rand() < 0.1 ? 'passed' : state,
      queue: 'amd_mi300_1',
      build_number: 1000 + index + (rand() < 0.1 ? (rand() < 0.5 ? -1 : 1) : 0),
      job_url: `https://buildkite.com/vllm/ci/builds/${1000 + index}/steps/canvas?jid=${kind}${j}`,
    }
    if (rand() < 0.1) r.observed_in_current_build = false
    if (rand() < 0.1) r.current_state = rand() < 0.5 ? 'failed' : 'soft'
    if (kind === 'fixed' && rand() < 0.3) r.current_url = `https://buildkite.com/vllm/ci/builds/${1000 + index}/steps/canvas?jid=c${j}`
    return r
  }
  const hard = n(2) && rand() < 0.5 ? n(2) : 0
  const soft = n(4)
  const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
  const HARD = ['failed', 'hard', 'failing', 'incident', 'error', 'timed_out', 'broken', 'canceled', 'cancelled', 'expired']
  const SOFT = ['soft', 'soft_fail', 'soft_failed']
  const failed = Array.from({ length: hard }, (_, j) => row('hard', j, pick(HARD)))
  const softRows = Array.from({ length: soft }, (_, j) => row('soft', j, pick(SOFT)))
  const current = [...failed, ...softRows]
  const split = n(current.length)
  const consistent = rand() < 0.8
  const fixed = Array.from({ length: n(4) }, (_, j) => row('fixed', j, 'passed'))
  const build: Rec = {
    number: 1000 + index,
    state: ['passed', 'failed', 'running', 'canceled', 'blocked'][n(4)],
    build_url: `https://buildkite.com/vllm/ci/builds/${1000 + index}`,
    total_groups: 50,
    failed_groups: failed,
    soft_failed_groups: softRows,
    failure_movement: {
      policy_id: 'observed-failure-movement-v1',
      available: rand() < 0.9,
      preceding_build_number: rand() < 0.9 ? 999 + index : 0,
      new: current.slice(0, split),
      recurring: consistent ? current.slice(split) : current.slice(split + 1),
      fixed,
    },
  }
  const summary: Rec = {
    latest_build_number: rand() < 0.8 ? build.number : build.number - 1,
    latest_job_variant_count: rand() < 0.9 ? 50 : 0,
    latest_job_variant_state_counts: { passed: 50 - hard - soft, hard, soft },
  }
  return { build, summary }
}

function summaryVariants(summary: Rec, rand: () => number): Rec {
  const s: Rec = structuredClone(summary)
  if (rand() < 0.3) s.latest_build_number = Math.floor(rand() * 3) + (s.latest_build_number || 0) - 1
  if (rand() < 0.2) s.latest_job_variant_count = 0
  if (rand() < 0.2) delete s.latest_job_variant_state_counts
  if (rand() < 0.3) s.latest_state_counts = { passed: 1, hard: Math.floor(rand() * 3), soft: Math.floor(rand() * 3) }
  return s
}

describe('v2 domain matches the classic renderer', () => {
  it('selects the same AMD cohort and retention note', () => {
    parity('latestAmd', latestAmd(shell), classic.latestAmd!(shell))
    expect(latestAmd(shell).builds.length).toBeGreaterThan(0)
    parity(
      'ciHealthPublicationRetentionMessage',
      ciHealthPublicationRetentionMessage(latestAmd(shell)),
      classic.ciHealthPublicationRetentionMessage!(classic.latestAmd!(shell)),
    )
    parity('currentAmdHealth', currentAmdHealth(shell.amd_test_health), classic.currentAmdHealth!(shell.amd_test_health))
    parity(
      'currentTestGroupParity',
      currentTestGroupParity(shell.test_group_parity),
      classic.currentTestGroupParity!(shell.test_group_parity),
    )
  })

  it('presents the published nightlies identically', async () => {
    const now = Date.parse(shell.generated_at) + 3600_000
    const summary = currentAmdHealth(shell.amd_test_health).summary || {}
    const builds = latestAmd(shell).builds as Rec[]
    for (const build of builds) {
      parity(
        'amdNightlyPresentation',
        amdNightlyPresentation(build, summary, shell.generated_at, now),
        classic.amdNightlyPresentation!(build, summary, shell.generated_at, now),
      )
    }
    await expectGoldenFile('nightly', 'published', builds.map((build) => amdNightlyPresentation(build, summary, shell.generated_at, now)))
  })

  it('agrees on 2,000 mutated nightlies, summaries, and evidence scopes', () => {
    const rand = rng(62363)
    const builds = latestAmd(shell).builds as Rec[]
    // Use the raw summary: the shell copy lacks scope fields, so currentAmdHealth
    // would reduce it to {} and leave the summary-count path untested.
    const summary = shell.amd_test_health.summary as Rec
    const base = Date.parse(shell.generated_at)
    for (let i = 0; i < 2000; i += 1) {
      const build = mutateBuild(builds[i % builds.length]!, rand)
      const s = summaryVariants(summary, rand)
      const now = base + Math.floor(rand() * 6) * 3600_000
      parity(
        'amdNightlyPresentation',
        amdNightlyPresentation(build, s, shell.generated_at, now),
        classic.amdNightlyPresentation!(build, s, shell.generated_at, now),
        `case ${i}`,
      )
      parity('nightlyFailureMovement', nightlyFailureMovement(build), classic.nightlyFailureMovement!(build))
      for (const key of ['new', 'recurring', 'fixed'] as const) {
        parity('nightlyFailureCount', nightlyFailureCount(build, key), classic.nightlyFailureCount!(build, key))
      }
      for (const scope of ['hard', 'soft', 'new', 'recurring', 'fixed', undefined, 'bogus']) {
        parity(
          'nightlyBuildEvidence',
          nightlyBuildEvidence(build, scope),
          classic.nightlyBuildEvidence!(build, scope),
          `case ${i} scope ${scope}`,
        )
      }
      parity('exactPipelineBuildUrl', exactPipelineBuildUrl(build, 'ci'), classic.exactPipelineBuildUrl!(build, 'ci'))
      for (const row of (build.failed_groups || []) as Rec[]) {
        parity('exactPipelineEvidenceUrl', exactPipelineEvidenceUrl(row, 'ci'), classic.exactPipelineEvidenceUrl!(row, 'ci'))
      }
    }
  })

  it('agrees on 3,000 synthetic nightlies covering every outcome', () => {
    const rand = rng(827)
    const base = Date.parse(shell.generated_at)
    const labels = new Set<string>()
    for (let i = 0; i < 3000; i += 1) {
      const { build, summary } = syntheticBuild(rand, i)
      const now = base + Math.floor(rand() * 6) * 3600_000
      const presented = amdNightlyPresentation(build, summary, shell.generated_at, now)
      labels.add(presented.label)
      parity(
        'amdNightlyPresentation',
        presented,
        classic.amdNightlyPresentation!(build, summary, shell.generated_at, now),
        `synthetic ${i}`,
      )
      for (const scope of ['hard', 'soft', 'new', 'recurring', 'fixed', undefined]) {
        parity('nightlyBuildEvidence', nightlyBuildEvidence(build, scope), classic.nightlyBuildEvidence!(build, scope))
      }
    }
    for (const label of [
      'Running with failures', 'Running clean', 'Canceled', 'Hard failures', 'Pipeline failed', 'Healthy',
      'Recovered', 'Soft observations', 'More failures', 'Improved', 'Changed, net even', 'Stable failure count',
    ]) {
      expect(labels, `outcome "${label}" never generated`).toContain(label)
    }
  })

  it('labels attention signals and population counts identically', () => {
    for (const kind of ['nightly_hard_failures', 'omni_waiting', 'queue_waiting', 'some_new_kind']) {
      parity('attentionLabel', attentionLabel({ kind }), classic.attentionLabel!({ kind }))
    }
    const payloads = [
      {},
      { count_semantics: 'complete' },
      { source_coverage: { population_semantics: 'complete', authoritative_complete: true } },
      { source_coverage: { population_semantics: 'complete', authoritative_complete: false } },
      { publication_retention: { complete_relative_to_source: false }, count_semantics: 'complete' },
    ]
    for (const payload of payloads) {
      const semantics = populationSemantics(payload)
      parity('populationSemantics', semantics, classic.populationSemantics!(payload))
      parity('observedCountLabel', observedCountLabel(1234, semantics), classic.observedCountLabel!(1234, semantics))
    }
  })
})

describe('nightly presentation on a hand-written nightly', () => {
  const build: Rec = {
    number: 1001,
    state: 'failed',
    build_url: 'https://buildkite.com/vllm/ci/builds/1001',
    total_groups: 10,
    failed_groups: [{ name: 'Kernels', state: 'failed', queue: 'amd_mi300_1', build_number: 1001, job_url: 'https://buildkite.com/vllm/ci/builds/1001/steps/canvas?jid=k1' }],
    soft_failed_groups: [{ name: 'Models', state: 'soft_fail', queue: 'amd_mi355_1', build_number: 1001, job_url: 'https://buildkite.com/vllm/ci/builds/1001/steps/canvas?jid=m1' }],
    failure_movement: {
      policy_id: 'observed-failure-movement-v1',
      available: true,
      preceding_build_number: 1000,
      new: [{ name: 'Kernels', state: 'failed', build_number: 1001 }],
      recurring: [{ name: 'Models', state: 'soft_fail', build_number: 1001 }],
      fixed: [{ name: 'Lora', state: 'passed', build_number: 1001 }],
    },
  }
  const summary = { latest_build_number: 1001, latest_job_variant_count: 10, latest_job_variant_state_counts: { passed: 8, hard: 1, soft: 1 } }

  it('labels the outcome, the movement, and the evidence', () => {
    expect(amdNightlyPresentation(build, summary, '2026-10-17T09:38:09Z', Date.parse('2026-10-17T10:38:09Z'))).toEqual({
      label: 'Hard failures',
      tone: 'hard',
      meta: '#1001 - 8 pass - 1 soft - 1 hard; No net change; Buildkite failed',
      hasSignal: true,
      movementLabel: 'No net change',
      movementMeta: '1 new - 1 recurring - 1 fixed',
      movementTone: 'soft',
      hasComparison: true,
      incidentCount: 2,
      incidentDelta: 0,
    })
    expect((['new', 'recurring', 'fixed'] as const).map((key) => nightlyFailureCount(build, key))).toEqual([1, 1, 1])
    expect(nightlyBuildEvidence(build, 'hard')).toEqual({ scope: 'hard', label: 'Current hard failures', available: true, rows: build.failed_groups })
  })

  it('maps every published state to its tone', () => {
    const tones = {
      pass: ['passed', 'green', 'healthy', 'fixed', 'success', 'PASSED'],
      hard: ['failed', 'failing', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'],
      soft: ['soft', 'soft_fail', 'soft_failed', 'soft_failing', 'warning', 'attention', 'elevated', 'waiting'],
      info: ['new', 'info', 'recurring'],
      neutral: ['', null, undefined, 'missing', 'not-targeted'],
    }
    for (const [tone, states] of Object.entries(tones)) expect(states.map(toneForState), tone).toEqual(states.map(() => tone))
  })

  it('labels attention signals and population counts', () => {
    expect(['nightly_hard_failures', 'omni_waiting', 'queue_waiting'].map((kind) => attentionLabel({ kind }))).toEqual([
      'Hard-failed groups in the latest AMD nightly',
      'Omni jobs waiting across the fleet',
      'queue waiting',
    ])
    expect([{}, { count_semantics: 'complete' }].map((p) => observedCountLabel(1234, populationSemantics(p)))).toEqual(['≥1,234', '1,234'])
  })
})
