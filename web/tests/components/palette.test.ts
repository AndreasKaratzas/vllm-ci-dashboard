import { describe, expect, it } from 'vitest'
import { readFileSync } from 'node:fs'
import { resolve } from 'node:path'
import { HEAT_LEVELS, SERIES, TONE_ICON, TONE_LABEL, hardwareColor, hardwareFamily, heatColor, heatLegend, heatLevel, RATE_THRESHOLDS, rateStatus, rateTone, statusVar, toneClasses, type TonePart } from '@/lib/palette'
import type { Tone } from '@/lib/tone'

const TONES: Tone[] = ['pass', 'hard', 'soft', 'info', 'neutral', 'not-targeted']
const PARTS: TonePart[] = ['badge', 'text', 'icon', 'fill', 'stroke', 'surface']

describe('palette', () => {
  it('normalizes hardware labels to one family color', () => {
    for (const label of ['MI355_DPX', 'mi355_8', 'MI355X', 'linux-mi355-1gpu']) {
      expect(hardwareFamily(label)).toBe('MI355')
      expect(hardwareColor(label)).toBe('var(--hw-mi355)')
    }
    expect(hardwareColor('MI300X')).toBe('var(--hw-mi300)')
    expect(hardwareColor('mi250')).toBe('var(--hw-mi250)')
    expect(hardwareColor('MI325')).toBe('var(--hw-mi325)')
  })

  it('renders unknown hardware neutral', () => {
    expect(hardwareFamily('H100')).toBeNull()
    expect(hardwareFamily('MI999')).toBeNull()
    expect(hardwareColor(undefined)).toBe(SERIES.muted)
  })

  it('maps rates to tones at 99 and 95 percent', () => {
    expect(rateTone(100)).toBe('pass')
    expect(rateTone(99)).toBe('pass')
    expect(rateTone(98.99)).toBe('soft')
    expect(rateTone(95)).toBe('soft')
    expect(rateTone(94.9)).toBe('hard')
    expect(rateTone(null)).toBe('neutral')
    expect(rateTone(Number.NaN)).toBe('neutral')
  })

  it('names the band of a non-passing rate from the same thresholds', () => {
    expect(RATE_THRESHOLDS).toEqual({ pass: 99, soft: 95 })
    expect(rateStatus(99)).toBeUndefined()
    expect(rateStatus(98.9)).toEqual({ tone: 'soft', label: 'Below 99%' })
    expect(rateStatus(93.3)).toEqual({ tone: 'hard', label: 'Below 95%' })
    expect(rateStatus(null)).toBeUndefined()
  })

  it('resolves every status to a theme token', () => {
    expect(statusVar('pass')).toBe('var(--status-pass)')
    expect(statusVar('hard')).toBe('var(--status-hard)')
    expect(statusVar('info')).toBe('var(--status-unknown)')
    expect(statusVar('not-targeted')).toBe('var(--status-canceled)')
    expect(statusVar('canceled')).toBe('var(--status-canceled)')
  })

  it('gives every tone an icon, a label and a class for every part', () => {
    for (const tone of TONES) {
      expect(TONE_ICON[tone]).toBeTruthy()
      expect(TONE_LABEL[tone]).toMatch(/\w/)
      for (const part of PARTS) expect(toneClasses(tone, part)).toMatch(/\S/)
    }
  })

  it('keeps info neutral and pass without a tint', () => {
    for (const part of PARTS) expect(toneClasses('info', part)).toBe(toneClasses('neutral', part))
    expect(toneClasses('pass', 'badge')).not.toMatch(/bg-status/)
    expect(toneClasses('hard', 'badge')).toMatch(/bg-status-hard\/12/)
    expect(toneClasses('soft', 'text')).toBe('text-status-soft-fg')
  })

  it('gives every count up to five its own heat step, then scales by the square root', () => {
    expect([0, -1, Number.NaN].map((v) => heatLevel(v, 4))).toEqual([0, 0, 0])
    expect(heatLevel(3, 0)).toBe(0)
    expect([1, 2, 3].map((v) => heatLevel(v, 3))).toEqual([2, 4, 5])
    expect([1, 2, 3, 4, 5].map((v) => heatLevel(v, 5))).toEqual([1, 2, 3, 4, 5])
    // One failure next to a maximum of 100 still reads above the empty step.
    expect([1, 25, 64, 100, 400].map((v) => heatLevel(v, 100))).toEqual([1, 3, 4, 5, 5])
  })

  it('names the heat steps as theme tokens and groups the legend by step', () => {
    expect(heatColor(0)).toBe('var(--heat-0)')
    expect(heatColor(HEAT_LEVELS + 3)).toBe(`var(--heat-${HEAT_LEVELS})`)
    expect(heatLegend(3)).toEqual([
      { level: 2, from: 1, to: 1 },
      { level: 4, from: 2, to: 2 },
      { level: 5, from: 3, to: 3 },
    ])
    expect(heatLegend(0)).toEqual([])
    const wide = heatLegend(40)
    expect(wide.map((step) => step.level)).toEqual([1, 2, 3, 4, 5])
    expect(wide.at(-1)!.to).toBe(40)
  })

  it('defines each heat step in both themes', () => {
    const css = readFileSync(resolve(import.meta.dirname, '../../src/styles/main.css'), 'utf8')
    const [light, dark] = css.split(/^\.dark \{/m)
    for (let level = 0; level <= HEAT_LEVELS; level++) {
      expect(light).toContain(`--heat-${level}: oklch(`)
      expect(dark).toContain(`--heat-${level}: oklch(`)
    }
    // More failures read darker on the light card and brighter on the dark card.
    const lightness = (block: string) => Array.from({ length: HEAT_LEVELS }, (_, i) => Number(new RegExp(`--heat-${i + 1}: oklch\\(([\\d.]+)`).exec(block)![1]))
    const down = lightness(light!)
    const up = lightness(dark!)
    expect(down).toEqual([...down].sort((a, b) => b - a))
    expect(up).toEqual([...up].sort((a, b) => a - b))
    expect(new Set([...down, ...up]).size).toBe(HEAT_LEVELS * 2)
  })

  it('keeps each heat ramp in one hue, never losing saturation, and inside sRGB', () => {
    const css = readFileSync(resolve(import.meta.dirname, '../../src/styles/main.css'), 'utf8')
    for (const block of css.split(/^\.dark \{/m)) {
      const steps = Array.from({ length: HEAT_LEVELS }, (_, i) => new RegExp(`--heat-${i + 1}: oklch\\(([\\d.]+) ([\\d.]+) ([\\d.]+)\\)`).exec(block)!.slice(1).map(Number))
      const chroma = steps.map(([, c]) => c!)
      expect(chroma).toEqual([...chroma].sort((a, b) => a - b))
      const hues = steps.map(([, , h]) => h!)
      expect(Math.max(...hues) - Math.min(...hues)).toBeLessThanOrEqual(6)
      // A color outside sRGB is clipped by the browser and shifts toward a paler, different hue.
      const channels = steps.flatMap(([l, c, h]) => linearSrgb(l!, c!, h!))
      expect(Math.min(...channels)).toBeGreaterThanOrEqual(-0.001)
      expect(Math.max(...channels)).toBeLessThanOrEqual(1.001)
    }
  })
})

function linearSrgb(l: number, c: number, h: number): number[] {
  const a = c * Math.cos((h * Math.PI) / 180)
  const b = c * Math.sin((h * Math.PI) / 180)
  const [x, y, z] = [l + 0.3963377774 * a + 0.2158037573 * b, l - 0.1055613458 * a - 0.0638541728 * b, l - 0.0894841775 * a - 1.291485548 * b].map((v) => v ** 3) as [number, number, number]
  return [4.0767416621 * x - 3.3077115913 * y + 0.2309699292 * z, -1.2684380046 * x + 2.6097574011 * y - 0.3413193965 * z, -0.0041960863 * x - 0.7034186147 * y + 1.707614701 * z]
}
