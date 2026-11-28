import { toneForState, type Rec, type Tone } from './format'
import { hasTestSignal, nightlyFailureCount, nightlyState } from './nightly'

export interface NightlyCounts {
  number: number
  createdAt: string
  total: number
  hard: number
  soft: number
  passed: number
  fixed: number | null
  passRate: number
}

/** Final-attempt pass/hard/soft counts of a nightly with test signal; null without a published passed count. */
export function nightlyCounts(build: Rec | null | undefined): NightlyCounts | null {
  if (!build || !hasTestSignal(build) || typeof build.passed_groups !== 'number') return null
  const total = Number(build.total_groups)
  const hard = Array.isArray(build.failed_groups) ? build.failed_groups.length : 0
  const soft = Array.isArray(build.soft_failed_groups) ? build.soft_failed_groups.length : 0
  const passed = build.passed_groups
  return {
    number: Number(build.number || 0),
    createdAt: String(build.created_at || ''),
    total,
    hard,
    soft,
    passed,
    fixed: nightlyFailureCount(build, 'fixed'),
    passRate: passed / total,
  }
}

/** Oldest-first counts for every retained nightly that produced a test signal. */
export function nightlySeries(builds: Rec[]): NightlyCounts[] {
  return builds
    .map(nightlyCounts)
    .filter((row): row is NightlyCounts => row !== null)
    .reverse()
}

/** The newest nightly's pass rate and its change in points versus the nightly with results before it. */
export function passRateTrend(builds: Rec[]): { latest: NightlyCounts; deltaPoints: number | null } | null {
  const series = nightlySeries(builds)
  const latest = series.at(-1)
  if (!latest) return null
  const previous = series.at(-2)
  return { latest, deltaPoints: previous ? (latest.passRate - previous.passRate) * 100 : null }
}

/** Tone of a nightly's outcome: hard or soft failures, a pass, or the tone of why it has no results. */
export function nightlyResultTone(build: Rec | null | undefined): Tone {
  const counts = nightlyCounts(build)
  if (!counts) return nightlyState(build)?.tone ?? 'neutral'
  return counts.hard ? 'hard' : counts.soft ? 'soft' : 'pass'
}

/** The most severe tone across attention signals; none active is a pass. */
export function attentionTone(items: Rec[]): Tone {
  const tones = items.map((item) => toneForState(item.severity))
  return tones.includes('hard') ? 'hard' : tones.includes('soft') ? 'soft' : 'pass'
}
