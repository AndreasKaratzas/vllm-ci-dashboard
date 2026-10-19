import { beforeAll, describe, expect, it } from 'vitest'
import * as v2 from '@/domain/ciHealth'
import type { Rec } from '@/domain/format'
import { classicParity, loadClassic, type ClassicFns } from './classic'
import { pinClock, readFixture } from './support/fixtures'
import { rng } from './support/rng'

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

const sections = readFixture<Rec>('health_sections.json')
const health: Rec = sections.amd_test_health
const parityPayload: Rec = sections.test_group_parity
pinClock(parityPayload.generated_at)
const parity = classicParity('ciHealth')
let classic: ClassicFns

beforeAll(() => {
  classic = loadClassic([...PORTED])
})

describe('CI Health domain matches the classic renderer', () => {
  it('reads the real AMD health snapshot identically', () => {
    const latest = health.summary.latest_build_number
    parity('amdHealthGroups', v2.amdHealthGroups(health), classic.amdHealthGroups!(health))
    parity('amdLogicalInventory', v2.amdLogicalInventory(health), classic.amdLogicalInventory!(health))
    expect(v2.amdLogicalInventory(health).rows.length).toBeGreaterThan(0)
    parity('logicalTestGroupPresentation', v2.logicalTestGroupPresentation(health.summary.latest_test_group_counts), classic.logicalTestGroupPresentation!(health.summary.latest_test_group_counts))
    for (const row of health.group_catalog as Rec[]) {
      parity('amdLatestState', v2.amdLatestState(row, latest), classic.amdLatestState!(row, latest))
      parity('amdGroupPassRate', v2.amdGroupPassRate(row), classic.amdGroupPassRate!(row))
      parity('amdGroupObservations', v2.amdGroupObservations(row), classic.amdGroupObservations!(row))
      for (const o of row.observations as Rec[]) {
        parity('observationState', v2.observationState(o), classic.observationState!(o))
        parity('isIncidentObservation', v2.isIncidentObservation(o), classic.isIncidentObservation!(o))
        parity('observationTimestamp', v2.observationTimestamp(o), classic.observationTimestamp!(o))
      }
    }
    for (const row of v2.amdLogicalInventory(health).rows as Rec[]) {
      for (const fn of ['amdLogicalStateLabel', 'amdLogicalStateTone'] as const) {
        parity(fn, v2[fn](row.state), classic[fn]!(row.state))
      }
      for (const variant of row.job_variants as Rec[]) {
        parity('amdLogicalCatalogGroup', v2.amdLogicalCatalogGroup(variant, health), classic.amdLogicalCatalogGroup!(variant, health))
        const byId = { id: variant.id }
        const byName = { exact_job_name: variant.exact_job_name }
        parity('amdLogicalCatalogGroup', v2.amdLogicalCatalogGroup(byId, health), classic.amdLogicalCatalogGroup!(byId, health))
        parity('amdLogicalCatalogGroup', v2.amdLogicalCatalogGroup(byName, health), classic.amdLogicalCatalogGroup!(byName, health))
        parity('amdLogicalSignalLabel', v2.amdLogicalSignalLabel(variant.test_signal_state), classic.amdLogicalSignalLabel!(variant.test_signal_state))
        parity('amdLogicalSignalTone', v2.amdLogicalSignalTone(variant.test_signal_state), classic.amdLogicalSignalTone!(variant.test_signal_state))
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
      parity('amdLatestState', v2.amdLatestState(row, latest), classic.amdLatestState!(row, latest))
      parity('amdGroupPassRate', v2.amdGroupPassRate(row), classic.amdGroupPassRate!(row))
      for (const s of ['passed', 'soft', 'hard', 'missing', 'unknown', 'x']) parity('amdStateLabel', v2.amdStateLabel(s), classic.amdStateLabel!(s))
      const counts: Rec = { total: Math.floor(rand() * 20), passing: Math.floor(rand() * 20), partial: Math.floor(rand() * 5) }
      if (rand() < 0.5) counts.passing_all = Math.floor(rand() * 10)
      if (rand() < 0.5) counts.non_passing = Math.floor(rand() * 10)
      if (rand() < 0.1) counts.available = false
      if (rand() < 0.1) delete counts.total
      parity('logicalTestGroupPresentation', v2.logicalTestGroupPresentation(counts), classic.logicalTestGroupPresentation!(counts))
      const obs: Rec = { [pick(['state', 'result', 'status'])]: pick(STATES), [pick(['observed_at', 'finished_at', 'created_at', 'date'])]: '2026-10-1' + (i % 9) }
      parity('isIncidentObservation', v2.isIncidentObservation(obs), classic.isIncidentObservation!(obs))
      parity('observationTimestamp', v2.observationTimestamp(obs), classic.observationTimestamp!(obs))
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
      parity('amdLogicalInventory', v2.amdLogicalInventory(copy), classic.amdLogicalInventory!(copy))
      expect(v2.amdLogicalInventory(copy).available).toBe(false)
    }
  })

  it('reads the real parity snapshot identically', () => {
    for (const stateName of ['all', 'action', 'existing', 'unsupported', 'bogus']) {
      for (const area of ['all', ...new Set((parityPayload.groups as Rec[]).map((g) => g.area))]) {
        parity('testGroupParityRows', v2.testGroupParityRows(parityPayload, stateName, area), classic.testGroupParityRows!(parityPayload, stateName, area))
      }
      parity('testGroupParityState', v2.testGroupParityState(stateName), classic.testGroupParityState!(stateName))
    }
    for (const row of parityPayload.groups as Rec[]) {
      parity('testGroupDefinitionState', v2.testGroupDefinitionState(row), classic.testGroupDefinitionState!(row))
    }
    for (const status of ['resolved', 'unresolved', 'ambiguous', 'unavailable', 'unconfigured', undefined, 'x']) {
      const row = { definition_resolution: status === undefined ? undefined : { status } }
      parity('testGroupDefinitionState', v2.testGroupDefinitionState(row), classic.testGroupDefinitionState!(row))
    }
  })
})
