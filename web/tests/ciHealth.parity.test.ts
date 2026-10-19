import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/ciHealth'
import type { Rec } from '@/domain/format'
import { loadClassic, readFixture, type ClassicFns } from './classic'

const PORTED = [
  'observationState',
  'isIncidentObservation',
  'observationTimestamp',
  'amdHealthGroups',
  'amdGroupObservations',
  'amdGroupPassRate',
  'amdLatestState',
  'amdStateLabel',
  'logicalTestGroupPresentation',
  'amdLogicalInventory',
  'amdLogicalStateLabel',
  'amdLogicalStateTone',
  'amdLogicalSignalLabel',
  'amdLogicalSignalTone',
  'amdLogicalCatalogGroup',
  'testGroupParityState',
  'testGroupParityRows',
  'testGroupDefinitionState',
] as const

let classic: ClassicFns
let health: Rec
let parity: Rec

function rng(seed: number) {
  let s = seed >>> 0
  return () => (s = (s * 1664525 + 1013904223) >>> 0) / 2 ** 32
}

beforeAll(() => {
  classic = loadClassic([...PORTED])
  const sections = readFixture<Rec>('health_sections.json')
  health = sections.amd_test_health
  parity = sections.test_group_parity
})

describe('CI Health domain matches the classic renderer', () => {
  it('reads the real AMD health snapshot identically', () => {
    const latest = health.summary.latest_build_number
    expect(v2.amdHealthGroups(health)).toEqual(classic.amdHealthGroups!(health))
    expect(v2.amdLogicalInventory(health)).toEqual(classic.amdLogicalInventory!(health))
    expect(v2.amdLogicalInventory(health).rows.length).toBeGreaterThan(0)
    expect(v2.logicalTestGroupPresentation(health.summary.latest_test_group_counts)).toEqual(
      classic.logicalTestGroupPresentation!(health.summary.latest_test_group_counts),
    )
    for (const row of health.group_catalog as Rec[]) {
      expect(v2.amdLatestState(row, latest)).toBe(classic.amdLatestState!(row, latest))
      expect(v2.amdGroupPassRate(row)).toBe(classic.amdGroupPassRate!(row))
      expect(v2.amdGroupObservations(row)).toEqual(classic.amdGroupObservations!(row))
      for (const o of row.observations as Rec[]) {
        expect(v2.observationState(o)).toBe(classic.observationState!(o))
        expect(v2.isIncidentObservation(o)).toBe(classic.isIncidentObservation!(o))
        expect(v2.observationTimestamp(o)).toBe(classic.observationTimestamp!(o))
      }
    }
    for (const row of v2.amdLogicalInventory(health).rows as Rec[]) {
      for (const fn of ['amdLogicalStateLabel', 'amdLogicalStateTone'] as const) {
        expect(v2[fn](row.state)).toBe(classic[fn]!(row.state))
      }
      for (const variant of row.job_variants as Rec[]) {
        expect(v2.amdLogicalCatalogGroup(variant, health)).toEqual(classic.amdLogicalCatalogGroup!(variant, health))
        const byId = { id: variant.id }
        const byName = { exact_job_name: variant.exact_job_name }
        expect(v2.amdLogicalCatalogGroup(byId, health)).toEqual(classic.amdLogicalCatalogGroup!(byId, health))
        expect(v2.amdLogicalCatalogGroup(byName, health)).toEqual(classic.amdLogicalCatalogGroup!(byName, health))
        expect(v2.amdLogicalSignalLabel(variant.test_signal_state)).toBe(classic.amdLogicalSignalLabel!(variant.test_signal_state))
        expect(v2.amdLogicalSignalTone(variant.test_signal_state)).toBe(classic.amdLogicalSignalTone!(variant.test_signal_state))
      }
    }
  })

  it('agrees on generated health rows, states, and summaries', () => {
    const rand = rng(62298)
    const pick = <T,>(xs: T[]) => xs[Math.floor(rand() * xs.length)]!
    const STATES = ['passed', 'soft', 'soft_fail', 'soft_failed', 'hard', 'failed', 'timed_out', 'broken', 'canceled', 'incident', 'error', 'expired', 'unknown', '', 'PASSED', 'FAILED', 'Soft_Fail', 'TIMED_OUT']
    for (let i = 0; i < 3000; i += 1) {
      const row: Rec = {
        latest_build_number: 100 + Math.floor(rand() * 3),
        latest_state: pick(STATES),
        passed: Math.floor(rand() * 5),
        soft_failed: Math.floor(rand() * 3),
        hard_failed: Math.floor(rand() * 3),
      }
      if (rand() < 0.3) row.pass_rate_pct = rand() < 0.5 ? rand() * 100 : 'n/a'
      const latest = rand() < 0.2 ? undefined : 101
      expect(v2.amdLatestState(row, latest)).toBe(classic.amdLatestState!(row, latest))
      expect(v2.amdGroupPassRate(row)).toBe(classic.amdGroupPassRate!(row))
      for (const s of ['passed', 'soft', 'hard', 'missing', 'unknown', 'x']) expect(v2.amdStateLabel(s)).toBe(classic.amdStateLabel!(s))
      const counts: Rec = { total: Math.floor(rand() * 20), passing: Math.floor(rand() * 20), partial: Math.floor(rand() * 5) }
      if (rand() < 0.5) counts.passing_all = Math.floor(rand() * 10)
      if (rand() < 0.5) counts.non_passing = Math.floor(rand() * 10)
      if (rand() < 0.1) counts.available = false
      if (rand() < 0.1) delete counts.total
      expect(v2.logicalTestGroupPresentation(counts)).toEqual(classic.logicalTestGroupPresentation!(counts))
      const obs: Rec = { [pick(['state', 'result', 'status'])]: pick(STATES), [pick(['observed_at', 'finished_at', 'created_at', 'date'])]: '2026-10-1' + (i % 9) }
      expect(v2.isIncidentObservation(obs)).toBe(classic.isIncidentObservation!(obs))
      expect(v2.observationTimestamp(obs)).toBe(classic.observationTimestamp!(obs))
    }
  })

  it('rejects logical inventories that do not reconcile, as classic does', () => {
    const mutations: ((h: Rec) => void)[] = [
      (h) => (h.latest_logical_test_groups.available = false),
      (h) => (h.latest_logical_test_groups.reconciliation.matches_latest_test_group_counts = false),
      (h) => (h.summary.latest_test_group_counts.build_number += 1),
      (h) => (h.summary.latest_test_group_counts.build_number -= 1),
      (h) => h.latest_logical_test_groups.rows.pop(),
      (h) => (h.source_pipeline = 'amd-ci'),
      (h) => (h.summary.latest_test_group_counts.available = false),
    ]
    for (const mutate of mutations) {
      const copy = structuredClone(health)
      mutate(copy)
      expect(v2.amdLogicalInventory(copy)).toEqual(classic.amdLogicalInventory!(copy))
      expect(v2.amdLogicalInventory(copy).available).toBe(false)
    }
  })

  it('reads the real parity snapshot identically', () => {
    for (const stateName of ['all', 'action', 'existing', 'unsupported', 'bogus']) {
      for (const area of ['all', ...new Set((parity.groups as Rec[]).map((g) => g.area))]) {
        expect(v2.testGroupParityRows(parity, stateName, area)).toEqual(classic.testGroupParityRows!(parity, stateName, area))
      }
      expect(v2.testGroupParityState(stateName)).toEqual(classic.testGroupParityState!(stateName))
    }
    for (const row of parity.groups as Rec[]) {
      expect(v2.testGroupDefinitionState(row)).toBe(classic.testGroupDefinitionState!(row))
    }
    for (const status of ['resolved', 'unresolved', 'ambiguous', 'unavailable', 'unconfigured', undefined, 'x']) {
      const row = { definition_resolution: status === undefined ? undefined : { status } }
      expect(v2.testGroupDefinitionState(row)).toBe(classic.testGroupDefinitionState!(row))
    }
  })
})
