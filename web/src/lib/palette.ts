import { CircleCheck, CircleDashed, CircleMinus, CircleX, Info, TriangleAlert } from '@lucide/vue'
import type { Component } from 'vue'
import type { Tone } from './tone'

// Charts accept only colors minted here, so every mark resolves to a theme token.
export type PaletteColor = string & { readonly __palette: unique symbol }
const token = (name: string) => `var(--${name})` as PaletteColor

export type StatusFill = 'pass' | 'soft' | 'hard' | 'unknown' | 'canceled'

const TONE_FILL: Record<Tone, StatusFill> = {
  pass: 'pass',
  soft: 'soft',
  hard: 'hard',
  info: 'unknown',
  neutral: 'unknown',
  'not-targeted': 'canceled',
}

export function statusVar(status: Tone | StatusFill): PaletteColor {
  return token(`status-${status in TONE_FILL ? TONE_FILL[status as Tone] : status}`)
}

/** The same color at a lower strength, for a secondary state of one meaning (e.g. recurring vs new failures). */
export function tint(color: PaletteColor, percent: number): PaletteColor {
  return `color-mix(in oklch, ${color} ${percent}%, transparent)` as PaletteColor
}

export const SERIES = {
  primary: token('series-1'),
  secondary: token('series-2'),
  muted: token('series-muted'),
} as const

/** Background of a time span with no observations (a collection gap), behind the series. */
export const CHART_GAP = { fill: 'color-mix(in oklch, var(--muted) 70%, transparent)' as PaletteColor, label: token('muted-foreground') } as const

export const HEAT_LEVELS = 5

/**
 * The step (0 for none, 1..HEAT_LEVELS) of a count up to `max`: linear while every count can get its
 * own step, then on a square-root scale so single failures stay visible next to a large maximum.
 */
export function heatLevel(value: number, max: number): number {
  if (!(value > 0) || !(max > 0)) return 0
  const share = Math.min(value, max) / max
  return Math.min(HEAT_LEVELS, Math.max(1, Math.ceil(HEAT_LEVELS * (max <= HEAT_LEVELS ? share : Math.sqrt(share)))))
}

export function heatColor(level: number): PaletteColor {
  return token(`heat-${Math.min(HEAT_LEVELS, Math.max(0, Math.round(level)))}`)
}

/** The whole-number counts each used step covers up to `max`, for the legend. */
export function heatLegend(max: number): { level: number; from: number; to: number }[] {
  const steps: { level: number; from: number; to: number }[] = []
  for (let value = 1; value <= Math.floor(max); value++) {
    const level = heatLevel(value, max)
    const last = steps.at(-1)
    if (last && last.level === level) last.to = value
    else steps.push({ level, from: value, to: value })
  }
  return steps
}

export type HardwareFamily = 'MI250' | 'MI300' | 'MI325' | 'MI355'
const HARDWARE_FAMILIES: readonly HardwareFamily[] = ['MI250', 'MI300', 'MI325', 'MI355']

/** MI355_DPX, mi355_8 and MI355X all belong to MI355; anything else is null. */
export function hardwareFamily(label: unknown): HardwareFamily | null {
  const match = /mi\s*-?(\d{3})/i.exec(String(label ?? ''))
  const family = match ? (`MI${match[1]}` as HardwareFamily) : null
  return family && HARDWARE_FAMILIES.includes(family) ? family : null
}

export function hardwareColor(label: unknown): PaletteColor {
  const family = hardwareFamily(label)
  return family ? token(`hw-${family.toLowerCase()}`) : SERIES.muted
}

/** The pass-rate bands every page shares: pass at or above `pass` percent, soft at or above `soft`, hard below. */
export const RATE_THRESHOLDS = { pass: 99, soft: 95 } as const

/** Pass at >= 99 %, soft at >= 95 %, hard below; a missing rate is neutral. */
export function rateTone(percent: number | null | undefined): Tone {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return 'neutral'
  if (percent >= RATE_THRESHOLDS.pass) return 'pass'
  if (percent >= RATE_THRESHOLDS.soft) return 'soft'
  return 'hard'
}

/** The tone of a rate with the band it falls in ("Below 95%"); pass and a missing rate are quiet, so undefined. */
export function rateStatus(percent: number | null | undefined): { tone: Tone; label: string } | undefined {
  const tone = rateTone(percent)
  if (tone === 'hard') return { tone, label: `Below ${RATE_THRESHOLDS.soft}%` }
  if (tone === 'soft') return { tone, label: `Below ${RATE_THRESHOLDS.pass}%` }
  return undefined
}

export type TonePart = 'badge' | 'text' | 'icon' | 'fill' | 'stroke' | 'surface'

// Literal class strings so Tailwind's scanner emits every one of them. Pass is
// the quiet default (no tint); 'info' deliberately renders neutral.
const QUIET: Record<TonePart, string> = {
  badge: 'border-border bg-muted text-foreground/80',
  text: 'text-muted-foreground',
  icon: 'text-muted-foreground',
  fill: 'bg-status-unknown',
  stroke: 'stroke-status-unknown',
  surface: 'border-border bg-muted/50',
}

const TONE_CLASSES: Record<Tone, Record<TonePart, string>> = {
  pass: {
    badge: 'border-transparent bg-transparent px-0 text-foreground',
    text: 'text-status-pass-fg',
    icon: 'text-status-pass',
    fill: 'bg-status-pass',
    stroke: 'stroke-status-pass',
    surface: 'border-status-pass/30 bg-status-pass/8',
  },
  soft: {
    badge: 'border-status-soft/40 bg-status-soft/12 text-status-soft-fg',
    text: 'text-status-soft-fg',
    icon: 'text-status-soft-fg',
    fill: 'bg-status-soft',
    stroke: 'stroke-status-soft',
    surface: 'border-status-soft/40 bg-status-soft/8',
  },
  hard: {
    badge: 'border-status-hard/30 bg-status-hard/12 text-status-hard-fg',
    text: 'text-status-hard-fg',
    icon: 'text-status-hard-fg',
    fill: 'bg-status-hard',
    stroke: 'stroke-status-hard',
    surface: 'border-status-hard/30 bg-status-hard/8',
  },
  info: QUIET,
  neutral: QUIET,
  'not-targeted': {
    badge: 'border-dashed border-border bg-transparent text-muted-foreground',
    text: 'text-muted-foreground',
    icon: 'text-muted-foreground',
    fill: 'bg-status-canceled',
    stroke: 'stroke-status-canceled',
    surface: 'border-dashed border-border bg-transparent',
  },
}

export function toneClasses(tone: Tone, part: TonePart): string {
  return TONE_CLASSES[tone][part]
}

export const TONE_ICON: Record<Tone, Component> = {
  pass: CircleCheck,
  soft: TriangleAlert,
  hard: CircleX,
  info: Info,
  neutral: CircleDashed,
  'not-targeted': CircleMinus,
}

export const TONE_LABEL: Record<Tone, string> = {
  pass: 'Passed',
  soft: 'Soft fail',
  hard: 'Hard fail',
  info: 'Info',
  neutral: 'Unknown',
  'not-targeted': 'Not targeted',
}
