import type { Rec } from './format'

export function isCanonicalAmdQueue(queue: unknown): boolean {
  const name = String(queue || '').trim().toLowerCase()
  return /^amd_mi\d{3,4}b?(?:_|$)/.test(name) && !name.startsWith('amd_mi355b')
}

export function isAmdMiHardware(hardware: unknown): boolean {
  return /^mi\d{3,4}$/.test(String(hardware || '').toLowerCase())
}

export function isAmdRuntimeJob(job: Rec | null | undefined): boolean {
  if (
    !job ||
    job.no_gpu === true ||
    job.source_no_gpu === true ||
    job.device === 'cpu' ||
    job.gpu_count === 0 ||
    job.num_gpus === 0
  )
    return false
  const raw = String(job.raw_name || job.name || job.job_name || '')
  if (/:(?:computer|amd):\s*\(\s*cpu(?:\s|\))/i.test(raw)) return false
  const queue = job.queue || job.q
  if (queue) return isCanonicalAmdQueue(queue)
  const prefix = raw.match(/^((?:amd_)?mi\d{3,4}b?(?:_[a-z0-9_-]+)?):/i)
  return Boolean(prefix && isCanonicalAmdQueue(prefix[1]!.startsWith('amd_') ? prefix[1] : 'amd_' + prefix[1]))
}

export function hardwareDisplayLabel(hardware: unknown): string {
  const id = String(hardware || 'unknown').toLowerCase()
  if (id === 'unknown') return 'Unknown'
  if (/^(?:mi\d|[abh]\d|l4$|t4$|cpu$|gpu$|npu$|tpu$)/.test(id)) return id.toUpperCase()
  return id
}

export function currentAmdHealth(payload: Rec | null | undefined): Rec {
  return payload &&
    payload.source_pipeline === 'ci' &&
    payload.job_scope === 'amd_gpu' &&
    payload.hardware_scope === 'amd_mi_gpu' &&
    (payload.group_catalog || []).every((row: Rec) => isAmdMiHardware(row.hardware) && isAmdRuntimeJob(row))
    ? payload
    : {
        available: false,
        source_pipeline: 'ci',
        job_scope: 'amd_gpu',
        hardware_scope: 'amd_mi_gpu',
        summary: {},
        group_catalog: [],
      }
}

export function currentTestGroupParity(payload: Rec | null | undefined): Rec {
  const source = (payload || {}).source || {}
  return source.pipeline === 'ci' && /^[0-9a-f]{40}$/.test(source.current_definition_commit_sha || '')
    ? payload!
    : { available: false, source: {}, summary: {}, groups: [] }
}

const ATTENTION_LABELS: Record<string, string> = {
  nightly_hard_failures: 'Hard-failed groups in the latest AMD nightly',
  nightly_infrastructure_blocked: 'AMD nightly blocked before test execution',
  nightly_soft_failures: 'Soft-failed groups in the latest AMD nightly',
  amd_logical_groups_not_fully_passing: 'AMD logical test groups not passing every route',
  omni_waiting: 'Omni jobs waiting across the fleet',
  main_slowdowns: 'AMD test steps on main running slower than before',
}

export function attentionLabel(item: Rec): string {
  return ATTENTION_LABELS[item.kind] || String(item.kind).replace(/_/g, ' ')
}

/** Home omits queue-derived signals; those live on their own views. */
export const HOME_EXCLUDED_ATTENTION = [
  'queue_waiting',
  'queue_zombies',
  'gating_red_targets',
  'target_groups_with_current_incidents',
  'mixed_state_flaky_candidates',
]
