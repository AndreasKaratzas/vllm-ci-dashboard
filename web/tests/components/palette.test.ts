import { describe, expect, it } from 'vitest'
import { SERIES, TONE_ICON, TONE_LABEL, hardwareColor, hardwareFamily, rateTone, statusVar, toneClasses, type TonePart } from '@/lib/palette'
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

  it('keeps info neutral and pass untinted', () => {
    for (const part of PARTS) expect(toneClasses('info', part)).toBe(toneClasses('neutral', part))
    expect(toneClasses('pass', 'badge')).not.toMatch(/bg-status/)
    expect(toneClasses('hard', 'badge')).toMatch(/bg-status-hard\/12/)
    expect(toneClasses('soft', 'text')).toBe('text-status-soft-fg')
  })
})
