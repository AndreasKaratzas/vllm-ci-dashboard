import type { Delta } from '@/components/stat/DeltaBadge.vue'

/** Change of the latest point against the previous one, e.g. "+2.5 pts" for a rate. */
export function trendDelta(values: number[], goodWhen: 'up' | 'down', unit = ''): Delta | undefined {
  if (values.length < 2) return undefined
  const change = Math.round((values.at(-1)! - values.at(-2)!) * 10) / 10
  const sign = change > 0 ? '+' : ''
  return { value: change, goodWhen, text: `${sign}${change.toFixed(1)}${unit ? ` ${unit}` : ''}` }
}

/** Change of the latest count against the previous one. */
export function pointDelta(values: number[], goodWhen: 'up' | 'down'): Delta | undefined {
  if (values.length < 2) return undefined
  return { value: values.at(-1)! - values.at(-2)!, goodWhen }
}
