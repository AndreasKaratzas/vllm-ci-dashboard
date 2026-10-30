import { describe, expect, it, vi } from 'vitest'
import { shortDate } from '@/domain/format'
import { addedMinutes, causeShort, commitRange, levelsText, sampleUrl, slowdowns } from '@/domain/slowdowns'
import { slowdownColumns, slowdownDetail, slowdownName } from '@/features/slowdowns/details'
import fixture from '../fixtures/main_ci_slowdowns.json'

const NOW = Date.parse('2026-10-20T20:00:00Z')
const data = slowdowns(fixture.main_ci)
const [family, single] = data.active

describe('slowdown table and sheet', () => {
  it('names a step without the :amd: prefix and gives levels, cause and builds their short forms', () => {
    expect(slowdownName(family!)).toBe('(MI300) V1 Attention (2 shards)')
    expect(levelsText(single!)).toBe('7.6m → 11.0m')
    expect([causeShort(single!), causeShort({ ...single!, cause: 'environment' })]).toEqual(['vLLM commits', 'Environment'])
    expect(sampleUrl(single!, 94223)).toBe('https://buildkite.com/vllm/ci/builds/94223')
    expect(sampleUrl({ ...single!, onset: { ...single!.onset, url: '' } }, 94223)).toBe('')
    expect(addedMinutes(data.active)).toBeCloseTo(19.2)
  })

  it('keeps step, increase and onset in the compact columns, with amber increases that never read as failures', () => {
    const open = vi.fn()
    const columns = slowdownColumns(open, NOW, { compact: true })
    expect(columns.map((column) => column.id)).toEqual(['step', 'increase', 'since'])
    const [step, increase, since] = columns.map((column) => column.cell!(single!))
    expect(increase).toMatchObject({ text: '+3.4m (+45%)', tone: 'soft', sub: 'since 8d ago' })
    expect(since).toMatchObject({ text: '#94275 → #94303', href: single!.onset.url, sub: shortDate(single!.onset.createdAt) })
    step!.onOpen!()
    expect(open).toHaveBeenCalledWith(single)
    const full = slowdownColumns(open, NOW)
    expect(full.find((column) => column.id === 'cause')!.cell!(single!)).toMatchObject({ text: commitRange(single!), href: single!.compareUrl, mono: true, sub: 'vLLM commits' })
    expect(full.every((column) => column.id === 'step' || column.id === 'increase' || column.class?.startsWith('hidden'))).toBe(true)
  })

  it('opens a sheet with both levels, the sampled builds and the onset range, never a stale published issue', () => {
    const detail = slowdownDetail(single!, data, NOW)
    expect(detail.stats!.map((stat) => [stat.label, stat.value])).toEqual([
      ['Before', '7.6m'],
      ['Now', '11.0m'],
      ['Increase', '+3.4m (+45%)'],
      ['Run-to-run noise', `±${single!.noiseMins!.toFixed(1)}m`],
    ])
    expect(detail.stats![2]).toMatchObject({ tone: 'soft', tint: true })
    expect(detail.sections![0]!.props).toEqual({ row: single })
    expect(detail.sections![0]!.meta).toBe('6 sampled runs before, 6 since')
    expect(detail.sources!.map((source) => source.label)).toEqual(['First slow build', 'Compare vLLM commits'])
    expect(detail.fields!.find((field) => field.label === 'Last fast build')).toMatchObject({ href: single!.lastGood!.url })
    expect(data).not.toHaveProperty('issue')
  })

  it('names the commits around the onset by SHA, and the latest rise only when it differs from the onset', () => {
    const range = `${single!.lastGood!.commit.slice(0, 12)}…${single!.onset.commit.slice(0, 12)}`
    expect(commitRange(single!)).toBe(range)
    expect(commitRange({ ...single!, lastGood: { ...single!.lastGood!, commit: single!.onset.commit } })).toBe(single!.onset.commit.slice(0, 12))
    const detail = slowdownDetail(single!, data, NOW)
    expect(detail.fields!.find((field) => field.label === 'vLLM commits')).toMatchObject({ value: range, href: single!.compareUrl })
    expect(detail.description).toContain(range)
    const field = (row: typeof single) => slowdownDetail(row!, data, NOW).fields!.find((f) => f.label === 'Latest rise')!.value
    expect(field({ ...single!, lastRise: single!.onset })).toBeNull()
    expect(field({ ...single!, lastRise: { ...single!.latest!, createdAt: '' } })).toBe(`#${single!.latest!.number}`)
  })
})
