import type { Tone } from '@/lib/tone'

export type { Tone }

// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Rec = Record<string, any>

export function value(v: unknown, fallback?: string): string {
  return v === null || v === undefined || v === '' ? fallback || '-' : String(v)
}

export function integer(v: unknown): string {
  if (v === null || v === undefined || v === '') return '-'
  return Number.isFinite(Number(v)) ? Number(v).toLocaleString() : '-'
}

export function percent(num: unknown, den: unknown, digits = 1): string {
  if (!Number.isFinite(Number(num)) || !Number.isFinite(Number(den)) || Number(den) <= 0) return '-'
  return ((Number(num) / Number(den)) * 100).toFixed(digits) + '%'
}

/** A percentage with one decimal ("93.3%"), or a dash when there is none. */
export function rateText(percent: number | null | undefined): string {
  return percent === null || percent === undefined || !Number.isFinite(percent) ? '—' : `${percent.toFixed(1)}%`
}

/** "1 event", "3 events". */
export function counted(count: number, singular: string, plural = `${singular}s`): string {
  return `${integer(count)} ${count === 1 ? singular : plural}`
}

export function duration(minutes: unknown): string {
  if (minutes === null || minutes === undefined || minutes === '') return '-'
  if (!Number.isFinite(Number(minutes))) return '-'
  const m = Number(minutes)
  if (m >= 1440) return (m / 1440).toFixed(m >= 2880 ? 0 : 1) + 'd'
  if (m >= 60) return Math.floor(m / 60) + 'h ' + Math.round(m % 60) + 'm'
  return m.toFixed(m < 10 ? 1 : 0) + 'm'
}

/** A run time in seconds: "42s", "1m 24s", "14m", "4h 12m", "1d 3h"; a dash when missing. */
export function seconds(value: unknown): string {
  if (value === null || value === undefined || value === '' || !Number.isFinite(Number(value))) return '-'
  const s = Math.max(0, Math.round(Number(value)))
  if (s < 60) return `${s}s`
  if (s < 3600) return s % 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s / 60}m`
  const m = Math.round(s / 60)
  if (m < 1440) return m % 60 ? `${Math.floor(m / 60)}h ${m % 60}m` : `${m / 60}h`
  const h = Math.round(m / 60)
  return h % 24 ? `${Math.floor(h / 24)}d ${h % 24}h` : `${h / 24}d`
}

/** Agent time summed over runs: seconds() under an hour, then decimal hours ("14.3 h", "388 h"). */
export function agentHours(s: number): string {
  if (s < 3600) return seconds(s)
  return s < 99.95 * 3600 ? `${(s / 3600).toFixed(1)} h` : `${Math.round(s / 3600)} h`
}

export function shortDate(ts: unknown): string {
  if (!ts) return '-'
  const d = new Date(String(ts))
  if (Number.isNaN(d.getTime())) return String(ts).slice(0, 10)
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' })
}

export function age(ts: unknown, nowMs: number = Date.now()): string {
  if (!ts) return 'timestamp unavailable'
  const delta = nowMs - new Date(String(ts)).getTime()
  if (!Number.isFinite(delta)) return 'timestamp unavailable'
  const mins = Math.max(0, Math.floor(delta / 60000))
  if (mins < 2) return 'just now'
  if (mins < 60) return mins + 'm ago'
  const hours = Math.floor(mins / 60)
  if (hours < 48) return hours + 'h ago'
  return Math.floor(hours / 24) + 'd ago'
}

export function toneForState(s: unknown): Tone {
  const stateName = String(s || '').toLowerCase()
  if (['passed', 'green', 'healthy', 'fixed', 'success'].includes(stateName)) return 'pass'
  if (['failed', 'failing', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'].includes(stateName))
    return 'hard'
  if (['soft', 'soft_fail', 'soft_failed', 'soft_failing', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName))
    return 'soft'
  if (['new', 'info', 'recurring'].includes(stateName)) return 'info'
  return 'neutral'
}

export function populationSemantics(payload: unknown): 'complete' | 'lower_bound' {
  const p = (payload || {}) as Rec
  const c = (p.source_coverage || {}) as Rec
  if (
    p.count_semantics === 'lower_bound' ||
    c.population_semantics === 'lower_bound' ||
    c.authoritative_complete === false ||
    (p.publication_retention || {}).complete_relative_to_source === false
  )
    return 'lower_bound'
  return p.count_semantics === 'complete' || (c.population_semantics === 'complete' && c.authoritative_complete === true)
    ? 'complete'
    : 'lower_bound'
}

export function observedCountLabel(count: unknown, semantics: string): string {
  const rendered = integer(count)
  if (rendered === '-') return rendered
  return semantics === 'lower_bound' ? '≥' + rendered : rendered
}

/** A commit SHA as every page shows it: its first 12 characters. */
export function shortSha(sha: unknown): string {
  return String(sha || '').slice(0, 12)
}

/** A published machine word as a phrase: "soft_confirmed" -> "Soft confirmed"; '' stays ''. */
export function sentence(value: unknown): string {
  const text = String(value || '').replace(/_/g, ' ').trim()
  return text.charAt(0).toUpperCase() + text.slice(1)
}

/** A published machine reason as a sentence: "strict_all_main_reliability_unavailable" -> "Strict all main reliability unavailable". */
export function reasonText(reason: unknown): string {
  return sentence(reason) || 'Unavailable'
}
