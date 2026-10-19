// eslint-disable-next-line @typescript-eslint/no-explicit-any
export type Rec = Record<string, any>

export type Tone = 'is-success' | 'is-danger' | 'is-warning' | 'is-info' | 'is-neutral' | 'is-not-targeted'

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

export function duration(minutes: unknown): string {
  if (minutes === null || minutes === undefined || minutes === '') return '-'
  if (!Number.isFinite(Number(minutes))) return '-'
  const m = Number(minutes)
  if (m >= 1440) return (m / 1440).toFixed(m >= 2880 ? 0 : 1) + 'd'
  if (m >= 60) return Math.floor(m / 60) + 'h ' + Math.round(m % 60) + 'm'
  return m.toFixed(m < 10 ? 1 : 0) + 'm'
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
  if (['passed', 'green', 'healthy', 'fixed', 'success'].includes(stateName)) return 'is-success'
  if (['failed', 'failing', 'hard', 'incident', 'error', 'red', 'critical', 'surge', 'broken'].includes(stateName))
    return 'is-danger'
  if (['soft', 'soft_fail', 'soft_failed', 'soft_failing', 'warning', 'attention', 'elevated', 'waiting'].includes(stateName))
    return 'is-warning'
  if (['new', 'info', 'recurring'].includes(stateName)) return 'is-info'
  return 'is-neutral'
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
