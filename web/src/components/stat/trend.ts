import type { Delta } from './DeltaBadge.vue'

export interface KpiTrend {
  spark?: number[]
  delta?: Delta
}

const known = (values: (number | null)[]) => values.filter((v): v is number => v !== null && Number.isFinite(v))

/**
 * The change of the last known value against the one before; nothing without two known values.
 * A `unit` (e.g. ' pts') shows the change with one decimal; `label` names the baseline.
 */
export function lastDelta(values: (number | null)[], goodWhen?: Delta['goodWhen'], options: { unit?: string; label?: string } = {}): Delta | undefined {
  const points = known(values)
  if (points.length < 2) return undefined
  const change = Math.round((points.at(-1)! - points.at(-2)!) * 10) / 10
  const shown = options.unit === undefined ? change.toLocaleString('en-US') : `${change.toFixed(1)}${options.unit}`
  return { value: change, goodWhen, text: `${change > 0 ? '+' : ''}${shown}`, label: options.label }
}

/** A KPI's sparkline and its last change; nothing without two known points. */
export function kpiTrend(values: (number | null)[], goodWhen?: Delta['goodWhen'], unit?: string): KpiTrend {
  const points = known(values)
  if (points.length < 2) return {}
  return { spark: points, delta: lastDelta(points, goodWhen, { unit }) }
}
