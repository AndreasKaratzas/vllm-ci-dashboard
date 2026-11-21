import type { Rec } from './format'

export const DASHBOARD_REPO = 'https://github.com/AndreasKaratzas/vllm-ci-dashboard'
/** The hourly Data Collection runs, which run every watcher and publish the snapshot. */
export const DATA_COLLECTION_RUNS_URL = `${DASHBOARD_REPO}/actions/workflows/hourly-master.yml`

function pipelineUrlMatches(url: unknown, pipeline: string, requireJob: boolean, expectedBuild: unknown): boolean {
  if (!url || !pipeline) return false
  try {
    const parsed = new URL(String(url))
    if (parsed.protocol !== 'https:' || parsed.host !== 'buildkite.com') return false
    const parts = parsed.pathname.split('/').filter(Boolean)
    if (parts.length < 4 || parts[0] !== 'vllm' || parts[1] !== pipeline || parts[2] !== 'builds' || !/^\d+$/.test(parts[3]!))
      return false
    if (expectedBuild !== null && expectedBuild !== undefined && expectedBuild !== '' && String(expectedBuild) !== parts[3])
      return false
    const suffix = parts.slice(4)
    if (!requireJob) return suffix.length === 0
    if (suffix.length < 2 || suffix[0] !== 'steps') return false
    if (suffix[1] === 'canvas') return Boolean(parsed.searchParams.get('jid') || parsed.searchParams.get('sid'))
    return Boolean(suffix[1])
  } catch {
    return false
  }
}

function buildNumberOf(record: Rec) {
  return record.build_number !== undefined ? record.build_number : record.number
}

/** A Buildkite job URL for exactly this record's build in ``pipeline``, else ''. */
export function exactPipelineEvidenceUrl(record: Rec | null | undefined, pipeline: string): string {
  if (!record || !pipeline) return ''
  const urls = [record.job_url, record.step_url, record.url, record.latest_url, record.html_url]
  return urls.find((url) => pipelineUrlMatches(url, pipeline, true, buildNumberOf(record))) || ''
}

/** A Buildkite build URL for exactly this record's build in ``pipeline``, else ''. */
export function exactPipelineBuildUrl(record: Rec | null | undefined, pipeline: string): string {
  if (!record || !pipeline) return ''
  const urls = [record.build_url, record.url, record.html_url]
  return urls.find((url) => pipelineUrlMatches(url, pipeline, false, buildNumberOf(record))) || ''
}
