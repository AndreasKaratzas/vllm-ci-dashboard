// Differential parity: the v2 domain modules must reproduce the classic
// renderer's (docs/assets/js/ops-v2.js) decisions on the same inputs.
import { beforeAll, describe, expect, it } from 'vitest'
import { populationSemantics, observedCountLabel, type Rec } from '@/domain/format'
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
import { loadClassic, readFixture } from './classic'

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

let classic: ReturnType<typeof loadClassic>
let shell: Rec

beforeAll(() => {
  classic = loadClassic(EXPORTED)
  shell = readFixture<Rec>('operations_v2_manifest.json').shell
})

// Deterministic PRNG so failures reproduce.
function rng(seed: number) {
  let s = seed >>> 0
  return () => ((s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32)
}

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
    expect(latestAmd(shell)).toEqual(classic.latestAmd!(shell))
    expect(latestAmd(shell).builds.length).toBeGreaterThan(0)
    expect(ciHealthPublicationRetentionMessage(latestAmd(shell))).toBe(
      classic.ciHealthPublicationRetentionMessage!(classic.latestAmd!(shell)),
    )
    expect(currentAmdHealth(shell.amd_test_health)).toEqual(classic.currentAmdHealth!(shell.amd_test_health))
    expect(currentTestGroupParity(shell.test_group_parity)).toEqual(
      classic.currentTestGroupParity!(shell.test_group_parity),
    )
  })

  it('presents the published nightlies identically', () => {
    const now = Date.parse(shell.generated_at) + 3600_000
    const summary = currentAmdHealth(shell.amd_test_health).summary || {}
    for (const build of latestAmd(shell).builds as Rec[]) {
      expect(amdNightlyPresentation(build, summary, shell.generated_at, now)).toEqual(
        classic.amdNightlyPresentation!(build, summary, shell.generated_at, now),
      )
    }
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
      expect(amdNightlyPresentation(build, s, shell.generated_at, now), `case ${i}`).toEqual(
        classic.amdNightlyPresentation!(build, s, shell.generated_at, now),
      )
      expect(nightlyFailureMovement(build)).toEqual(classic.nightlyFailureMovement!(build))
      for (const key of ['new', 'recurring', 'fixed'] as const) {
        expect(nightlyFailureCount(build, key)).toBe(classic.nightlyFailureCount!(build, key))
      }
      for (const scope of ['hard', 'soft', 'new', 'recurring', 'fixed', undefined, 'bogus']) {
        expect(nightlyBuildEvidence(build, scope), `case ${i} scope ${scope}`).toEqual(
          classic.nightlyBuildEvidence!(build, scope),
        )
      }
      expect(exactPipelineBuildUrl(build, 'ci')).toBe(classic.exactPipelineBuildUrl!(build, 'ci'))
      for (const row of (build.failed_groups || []) as Rec[]) {
        expect(exactPipelineEvidenceUrl(row, 'ci')).toBe(classic.exactPipelineEvidenceUrl!(row, 'ci'))
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
      expect(presented, `synthetic ${i}`).toEqual(classic.amdNightlyPresentation!(build, summary, shell.generated_at, now))
      for (const scope of ['hard', 'soft', 'new', 'recurring', 'fixed', undefined]) {
        expect(nightlyBuildEvidence(build, scope)).toEqual(classic.nightlyBuildEvidence!(build, scope))
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
      expect(attentionLabel({ kind })).toBe(classic.attentionLabel!({ kind }))
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
      expect(semantics).toBe(classic.populationSemantics!(payload))
      expect(observedCountLabel(1234, semantics)).toBe(classic.observedCountLabel!(1234, semantics))
    }
  })
})
