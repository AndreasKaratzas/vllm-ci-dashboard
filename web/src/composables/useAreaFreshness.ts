import { computed, type ComputedRef, type Ref } from 'vue'
import type { Freshness } from '@/components/layout/PageHeader.vue'
import { age, type Rec } from '@/domain/format'
import { areaFreshness, sourceFreshness, type FreshnessState } from '@/domain/freshness'
import type { Tone } from '@/lib/tone'
import { useClock } from './useClock'

const TONE: Record<FreshnessState, Tone> = { fresh: 'pass', warn: 'soft', stale: 'hard' }

/** The PageHeader freshness chip for one page area: the age of its oldest collector source. */
export function useAreaFreshness(area: string, ops: Ref<Rec | null>): ComputedRef<Freshness | undefined> {
  const now = useClock()
  return computed(() => {
    if (!ops.value) return undefined
    const summary = areaFreshness(sourceFreshness(ops.value, null, now.value), area)
    if (!summary) return undefined
    const sources = summary.sources
      .map((source) => `${source.label} ${source.timestamp ? age(source.timestamp, now.value) : 'has no collection time'} (target ${source.warnMinutes} min)`)
      .join('; ')
    return {
      text: summary.timestamp ? `Collected ${age(summary.timestamp, now.value)}` : 'Collection time unknown',
      tone: TONE[summary.state],
      detail: `${sources}.`,
    }
  })
}
