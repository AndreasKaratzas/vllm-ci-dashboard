import type { Delta } from './DeltaBadge.vue'

export interface KpiTrend {
  spark?: number[]
  delta?: Delta
}

/**
 * A KPI's sparkline and the change of its last point against the one before; nothing without two
 * known points. `unit` is appended to the delta text with one decimal (e.g. ' pts').
 */
export function kpiTrend(values: (number | null)[], goodWhen?: Delta['goodWhen'], unit = ''): KpiTrend {
  const known = values.filter((v): v is number => v !== null && Number.isFinite(v))
  if (known.length < 2) return {}
  const change = Math.round((known.at(-1)! - known.at(-2)!) * 10) / 10
  const shown = unit ? change.toFixed(1) : String(change)
  return { spark: known, delta: { value: change, goodWhen, text: `${change > 0 ? '+' : ''}${shown}${unit}` } }
}
