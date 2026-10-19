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

export const SERIES = {
  primary: token('series-1'),
  secondary: token('series-2'),
  muted: token('series-muted'),
} as const

/** The same hue at reduced strength, e.g. a lighter step that reads as "less strict". */
export function tint(color: PaletteColor, percent: number): PaletteColor {
  return `color-mix(in oklch, ${color} ${percent}%, transparent)` as PaletteColor
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

/** Pass at >= 99 %, soft at >= 95 %, hard below; a missing rate is neutral. */
export function rateTone(percent: number | null | undefined): Tone {
  if (percent === null || percent === undefined || !Number.isFinite(percent)) return 'neutral'
  if (percent >= 99) return 'pass'
  if (percent >= 95) return 'soft'
  return 'hard'
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
