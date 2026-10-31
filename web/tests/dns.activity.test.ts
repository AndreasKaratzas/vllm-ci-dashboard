import { describe, expect, it } from 'vitest'
import { dnsActivity, dnsPresentation, queueDnsEvidenceForNode } from '@/domain/dns'
import type { Rec } from '@/domain/format'
import { pinClock, readFixture } from './support/fixtures'

const payload = readFixture<Rec>('dns_pages.json')
const now = pinClock(payload.generated_at)
const sum = (values: number[]) => values.reduce((total, value) => total + value, 0)

function activity(windowId: string, scope = 'amd') {
  const view = dnsPresentation(payload, windowId, scope, now)
  return { view, activity: dnsActivity(payload, view)! }
}

describe('dnsActivity', () => {
  it('buckets 30 days by day and counts each retained job once, by outcome', () => {
    const { view, activity: a } = activity('720h')
    const retained = view.cards.flatMap((card) => card.bars.flatMap((bar) => queueDnsEvidenceForNode(payload, '720h', card.queue.queue, bar.node.nodeRaw)))
    expect(a.bucketMs).toBe(86_400_000)
    expect(a.points).toHaveLength(30)
    expect(a.points[0]!.start).toBe(Date.parse(view.block!.start))
    expect(a.retained).toBe(retained.length)
    expect(sum(a.points.map((p) => p.jobs))).toBe(retained.length)
    expect(sum(a.points.map((p) => p.hard))).toBe(retained.filter((row) => row.state === 'failed' || row.state === 'hard').length)
    expect(sum(a.points.map((p) => p.passed))).toBe(retained.filter((row) => row.state === 'passed').length)
    expect(sum(a.points.map((p) => p.huggingface))).toBe(retained.filter((row) => row.target_categories.includes('huggingface_hub')).length)
    for (const p of a.points) {
      expect(p.passed + p.soft + p.hard + p.unknown).toBe(p.jobs)
      expect(p.nodes).toBeLessThanOrEqual(p.jobs)
      expect(p.queues).toBeLessThanOrEqual(p.nodes)
    }
    // The trimmed fixture keeps 50 of the 372 published jobs, so the series is a lower bound.
    expect(a.complete).toBe(false)
    expect(dnsActivity(payload, { ...view, totals: { ...view.totals, affectedJobs: retained.length } })!.complete).toBe(true)
  })

  it('uses finer buckets for shorter windows', () => {
    expect(activity('168h').activity.bucketMs).toBe(6 * 3_600_000)
    expect(activity('168h').activity.points).toHaveLength(28)
    expect(activity('24h').activity.bucketMs).toBe(3_600_000)
    expect(activity('3h').activity.bucketMs).toBe(15 * 60_000)
  })

  it('follows the selected scope', () => {
    const all = activity('720h', 'amd').activity
    const canonical = activity('720h', 'canonical')
    const queues = new Set(canonical.view.cards.map((card) => card.queue.queue))
    const inScope = (payload.evidence.items as Rec[]).filter((item) => queues.has(item.queue)).length
    expect(canonical.activity.retained).toBe(inScope)
    expect(canonical.activity.retained).toBeLessThanOrEqual(all.retained)
  })

  it('is empty, not missing, for a window without observations', () => {
    const { activity: a } = activity('3h')
    expect(a.retained).toBe(0)
    expect(a.points.every((p) => p.jobs === 0)).toBe(true)
  })

  it('is null when the window is absent', () => {
    const view = dnsPresentation({ ...payload, windows: {} }, '720h', 'amd', now)
    expect(dnsActivity(payload, view)).toBeNull()
  })
})
