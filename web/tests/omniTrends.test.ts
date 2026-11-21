import { describe, expect, it } from 'vitest'
import { emptyOmniMappingStats, isRetiringQueue, omniKpiTrends, omniQueueSeries, type MappingBucket } from '@/domain/omni'

const HOUR = 3_600_000

function bucket(i: number, omni: Record<string, number>, main: number, complete = true): MappingBucket {
  const stats = (byQueue: Record<string, number>) => ({
    ...emptyOmniMappingStats(),
    mapped_jobs: Object.values(byQueue).reduce((a, b) => a + b, 0),
    mapped_gpu_slots: 2 * Object.values(byQueue).reduce((a, b) => a + b, 0),
    by_queue: Object.fromEntries(Object.entries(byQueue).map(([q, n]) => [q, { mapped_jobs: n }])),
  })
  return {
    id: String(i),
    start: i * HOUR,
    end: (i + 1) * HOUR,
    rows: [],
    complete,
    lowerBound: false,
    hasOpenBucket: !complete,
    workloads: { omni: stats(omni), main: stats({ amd_mi300_1: main }) },
  }
}

// Four closed buckets and an open one: the older closed half is 0-1, the newer 2-3.
const buckets = [
  bucket(0, { amd_mi300_1: 10 }, 30),
  bucket(1, { amd_mi300_1: 10, amd_mi325_1: 10 }, 20),
  bucket(2, { amd_mi300_1: 30 }, 30),
  bucket(3, { amd_mi300_1: 20, amd_mi325_4: 10 }, 30),
  bucket(4, { amd_mi300_1: 5 }, 0, false),
]

describe('omniKpiTrends', () => {
  it('trends counts per bucket and compares the newer closed half with the older one', () => {
    const trends = omniKpiTrends(buckets, true)
    expect(trends['omni-mapped-jobs']).toEqual({ series: [10, 20, 30, 30, 5], change: { kind: 'percent', value: 100 } })
    expect(trends['omni-gpu-demand']!.series).toEqual([20, 40, 60, 60, 10])
  })

  it('trends shares in percent and changes them in points', () => {
    const trends = omniKpiTrends(buckets, true)
    // Omni / (Omni + main): older 30/80 = 37.5 %, newer 60/120 = 50 %.
    expect(trends['omni-mapped-share']!.series).toEqual([25, 50, 50, 50, 100])
    expect(trends['omni-mapped-share']!.change).toEqual({ kind: 'points', value: 12.5 })
    // MI325 / Omni: older 10/30, newer 10/60.
    expect(trends['omni-retiring-share']!.series).toEqual([0, 50, 0, (10 / 30) * 100, 0])
    expect(trends['omni-retiring-share']!.change).toEqual({ kind: 'points', value: -16.7 })
  })

  it('withholds shares and every change when coverage does not support rates', () => {
    const trends = omniKpiTrends(buckets, false)
    expect(trends['omni-mapped-share']).toEqual({ series: null, change: null })
    expect(trends['omni-retiring-share']).toEqual({ series: null, change: null })
    expect(trends['omni-mapped-jobs']).toEqual({ series: [10, 20, 30, 30, 5], change: null })
  })

  it('draws nothing for a single bucket or an all-zero measure', () => {
    expect(omniKpiTrends(buckets.slice(0, 1), true)['omni-mapped-jobs']).toEqual({ series: null, change: null })
    const quiet = [bucket(0, { amd_mi300_1: 1 }, 1), bucket(1, { amd_mi300_1: 1 }, 1)]
    expect(omniKpiTrends(quiet, true)['omni-retiring-share']).toEqual({ series: null, change: null })
  })
})

describe('Omni queue helpers', () => {
  it('knows the retiring MI325 queues', () => {
    expect(['amd_mi325_1', 'amd_mi300_1', 'amd_mi355_1'].map(isRetiringQueue)).toEqual([true, false, false])
  })

  it('reads one queue across the buckets', () => {
    expect(omniQueueSeries(buckets, 'amd_mi325_1')).toEqual([0, 10, 0, 0, 0])
  })
})
