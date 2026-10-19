const HEALTHY_MAX_AGE_MS = 3 * 60 * 60 * 1000
const FUTURE_SKEW_MS = 5 * 60 * 1000

export type Tone = 'warning' | 'critical'

export interface StatusView {
  tone: Tone
  title: string
  message: string
}

export interface PublicationStatus {
  schema_version?: number
  mode?: string
  status?: string
  generated_at?: string
  degraded_since?: string | null
  publication_blocked?: boolean
  uses_fallback?: boolean
  affected_surfaces?: unknown[]
  affected_surface_count?: number
  fallback_surface_count?: number
  fresh_degraded_surface_count?: number
}

const MODE_VIEWS: Record<string, StatusView> = {
  stale: {
    tone: 'critical',
    title: 'Dashboard snapshot is stale',
    message:
      'No successful dashboard refresh has been published in the last three hours. Build and test states may have changed since this snapshot.',
  },
  degraded: {
    tone: 'warning',
    title: 'Live data is degraded',
    message:
      'One or more dashboard areas did not pass freshness or consistency checks. Current data remains visible while the issue is investigated.',
  },
  fallback: {
    tone: 'warning',
    title: 'Using last-known-good data',
    message: 'Some dashboard areas failed validation, so a previously validated snapshot is being shown.',
  },
  mixed: {
    tone: 'warning',
    title: 'Dashboard data is partially recovered',
    message: 'Some areas use last-known-good data while other areas are showing current degraded data.',
  },
  blocked: {
    tone: 'critical',
    title: 'Latest dashboard refresh blocked',
    message:
      'The latest data refresh did not pass publication checks. The dashboard remains on the last published snapshot.',
  },
  unavailable: {
    tone: 'warning',
    title: 'Publication status unavailable',
    message:
      'The dashboard could not verify whether this snapshot is current. Treat displayed data as potentially stale.',
  },
}

export function safeTimestamp(value: unknown): string {
  if (typeof value !== 'string' || !value || value.length > 64) return ''
  const zonedIso = /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})$/
  if (!zonedIso.test(value) || Number.isNaN(Date.parse(value))) return ''
  return value
}

function hasHealthyCurrentContract(payload: PublicationStatus) {
  return (
    payload.schema_version === 1 &&
    payload.mode === 'current' &&
    payload.status === 'healthy' &&
    payload.degraded_since === null &&
    payload.publication_blocked === false &&
    payload.uses_fallback === false &&
    Array.isArray(payload.affected_surfaces) &&
    payload.affected_surfaces.length === 0 &&
    payload.affected_surface_count === 0 &&
    payload.fallback_surface_count === 0 &&
    payload.fresh_degraded_surface_count === 0
  )
}

/** Banner for a publication status payload, or null when it is healthy and current. */
export function viewFor(payload: unknown, nowMs: number = Date.now()): StatusView | null {
  if (!payload || typeof payload !== 'object') return MODE_VIEWS.unavailable!
  const status = payload as PublicationStatus
  // Blocked outranks staleness; every other mode must still obey the snapshot
  // freshness SLO, because an old degraded record means reconciliation stopped.
  if (status.mode === 'blocked') return MODE_VIEWS.blocked!
  const generatedAt = safeTimestamp(status.generated_at)
  if (!generatedAt) return MODE_VIEWS.unavailable!
  const generatedMs = Date.parse(generatedAt)
  if (generatedMs - nowMs > FUTURE_SKEW_MS) return MODE_VIEWS.unavailable!
  if (nowMs - generatedMs > HEALTHY_MAX_AGE_MS) return MODE_VIEWS.stale!
  if (status.mode === 'current' && status.status === 'healthy') {
    return hasHealthyCurrentContract(status) ? null : MODE_VIEWS.unavailable!
  }
  if (status.mode === 'current' && status.status === 'degraded') return MODE_VIEWS.degraded!
  return MODE_VIEWS[status.mode ?? ''] ?? MODE_VIEWS.unavailable!
}

export function statusDetails(payload: unknown): string[] {
  const status = (payload && typeof payload === 'object' ? payload : {}) as PublicationStatus
  const details: string[] = []
  const areas = Array.isArray(status.affected_surfaces)
    ? [...new Set(status.affected_surfaces.filter((v): v is string => typeof v === 'string' && v.length <= 64))].slice(0, 8)
    : []
  if (areas.length) {
    details.push(`Affected areas: ${areas.join(', ')}.`)
  } else if (Number.isInteger(status.affected_surface_count) && status.affected_surface_count! > 0) {
    const count = Math.min(status.affected_surface_count!, 99)
    details.push(`${count} dashboard ${count === 1 ? 'area is' : 'areas are'} affected.`)
  }
  const degradedSince = safeTimestamp(status.degraded_since)
  const generatedAt = safeTimestamp(status.generated_at)
  if (degradedSince) details.push(`Degraded since: ${degradedSince}.`)
  if (generatedAt) details.push(`Status recorded: ${generatedAt}.`)
  return details
}
