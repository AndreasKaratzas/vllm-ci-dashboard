import { shortDayTime } from '@/components/charts/chart'
import type { Detail, DetailField } from '@/components/evidence/detail'
import { badge, link, num, text, type ColumnSpec, type FilterSpec } from '@/components/table/columns'
import { CACHE_LABELS, cacheClass, dockerJobUrl, stepLabel, stepShort, type DockerJob, type RebuildEpisode } from '@/domain/dockerBuilds'
import { agentHours, integer, seconds } from '@/domain/format'
import type { Tone } from '@/lib/tone'

const STATE_TONE: Record<string, Tone> = {
  passed: 'pass',
  failed: 'hard',
  timed_out: 'hard',
  canceled: 'not-targeted',
  running: 'info',
  pending: 'info',
}
const STATE_LABEL: Record<string, string> = { timed_out: 'Timed out' }
export const stateLabel = (state: string) => STATE_LABEL[state] ?? state.charAt(0).toUpperCase() + state.slice(1)
const stateTone = (state: string): Tone => STATE_TONE[state] ?? 'neutral'
export const sourceLabel = (source: string) => (source === 'pr' ? 'PR' : source === 'main' ? 'main' : 'Other branch')

const runCell = (job: DockerJob) => {
  if (job.runS === null && job.state === 'running') return { text: 'Running', nowrap: true }
  const cls = cacheClass(job)
  return { ...num(job.runS, seconds), sub: cls ? CACHE_LABELS[cls] : undefined, nowrap: true }
}

export function jobColumns(): ColumnSpec<DockerJob>[] {
  return [
    { id: 'created', label: 'Created (UTC)', value: (job) => job.createdMs, cell: (job) => ({ text: shortDayTime(job.createdMs), nowrap: true }), class: 'hidden whitespace-nowrap sm:table-cell' },
    { id: 'step', label: 'Step', value: (job) => stepLabel(job.step), cell: (job) => ({ ...text(stepShort(job.step), job.retried ? 'Retried attempt' : undefined), title: stepLabel(job.step), nowrap: true }) },
    { id: 'run', label: 'Run time', value: (job) => job.runS ?? -1, cell: runCell, numeric: true },
    { id: 'wait', label: 'Wait', value: (job) => job.waitS ?? -1, cell: (job) => num(job.waitS, seconds), numeric: true, class: 'hidden md:table-cell' },
    { id: 'state', label: 'State', value: (job) => job.state, cell: (job) => badge(stateLabel(job.state), stateTone(job.state)), class: 'hidden sm:table-cell' },
    { id: 'source', label: 'Source', value: (job) => sourceLabel(job.source), class: 'hidden sm:table-cell' },
    { id: 'pipeline', label: 'Pipeline', value: (job) => job.pipeline, class: 'hidden lg:table-cell' },
    { id: 'build', label: 'Build', value: (job) => job.build, cell: (job) => link(`#${job.build}`, dockerJobUrl(job), true), numeric: true },
  ]
}

const options = (values: string[], label: (value: string) => string) => values.map((value) => ({ value, label: label(value) }))

export function jobFilters(jobs: DockerJob[]): FilterSpec<DockerJob>[] {
  const present = (pick: (job: DockerJob) => string) => [...new Set(jobs.map(pick))].sort()
  return [
    {
      id: 'run',
      label: 'run times',
      allLabel: 'Any run time',
      initial: '600',
      options: [
        { value: '600', label: 'At least 10 min' },
        { value: '1800', label: 'At least 30 min' },
        { value: '7200', label: 'At least 2 h' },
      ],
      predicate: (job, value) => (job.runS === null ? job.state === 'running' : job.runS >= Number(value)),
    },
    { id: 'step', label: 'steps', options: options(present((job) => job.step), stepLabel), predicate: (job, value) => job.step === value },
    { id: 'source', label: 'sources', options: options(present((job) => job.source), sourceLabel), predicate: (job, value) => job.source === value },
    { id: 'state', label: 'states', options: options(present((job) => job.state), stateLabel), predicate: (job, value) => job.state === value },
  ]
}

export const jobSearch = (job: DockerJob) => `${job.step} ${job.id} ${job.build}`

export function jobDetail(job: DockerJob): Detail {
  const url = dockerJobUrl(job)
  const cls = cacheClass(job)
  const fields: DetailField[] = [
    { label: 'Step', value: stepLabel(job.step) },
    { label: 'Step key', value: job.step, mono: true },
    { label: 'Pipeline', value: `vllm/${job.pipeline}`, mono: true },
    { label: 'Build', value: `#${job.build}`, href: url, mono: true },
    { label: 'Source', value: sourceLabel(job.source) },
    { label: 'State', value: stateLabel(job.state), tone: stateTone(job.state) },
    { label: 'Created', value: `${shortDayTime(job.createdMs)} UTC` },
    { label: 'Wait for an agent', value: job.waitS === null ? null : seconds(job.waitS), hint: 'Not started' },
    { label: 'Run time', value: job.runS === null ? null : seconds(job.runS), hint: 'Not finished' },
    { label: 'Exit status', value: job.exit, mono: true, hint: 'No exit status' },
    { label: 'Attempt', value: job.retried ? 'Retried (excluded from run-time statistics)' : 'Final attempt' },
  ]
  if (cls) fields.splice(9, 0, { label: 'Base image', value: CACHE_LABELS[cls] })
  return {
    id: `docker-job:${job.id}`,
    title: `${stepLabel(job.step)} · #${job.build}`,
    subtitle: `vllm/${job.pipeline} · ${sourceLabel(job.source)} build`,
    fields,
    sources: [{ label: 'Buildkite job', url }],
    width: 'md',
  }
}

const SOURCE_ORDER = ['main', 'pr']
const episodeSpan = (ep: RebuildEpisode) => (ep.lastMs === ep.firstMs ? 'single' : seconds((ep.lastMs - ep.firstMs) / 1000))
const episodeTime = (ep: RebuildEpisode) => (ep.rebuilding ? `${agentHours(ep.rebuildS + ep.runningS)} so far` : agentHours(ep.rebuildS))
export const episodeSources = (ep: RebuildEpisode) =>
  Object.entries(ep.sources)
    .sort(([a], [b]) => (SOURCE_ORDER.indexOf(a) + 1 || 9) - (SOURCE_ORDER.indexOf(b) + 1 || 9))
    .map(([source, count]) => `${sourceLabel(source)} ${integer(count)}`)
    .join(' · ')

export function episodeColumns(): ColumnSpec<RebuildEpisode>[] {
  return [
    { id: 'started', label: 'Started (UTC)', value: (ep) => ep.firstMs, cell: (ep) => ({ text: shortDayTime(ep.firstMs), nowrap: true }) },
    { id: 'span', label: 'Span', value: (ep) => ep.lastMs - ep.firstMs, cell: (ep) => ({ text: episodeSpan(ep), nowrap: true }), numeric: true, class: 'hidden sm:table-cell' },
    { id: 'rebuilds', label: 'Rebuilds', value: (ep) => ep.jobs.length, cell: (ep) => ({ ...num(ep.jobs.length), sub: ep.rebuilding ? `${integer(ep.rebuilding)} running` : undefined }), numeric: true },
    { id: 'full', label: 'Full', value: (ep) => ep.full, cell: (ep) => ({ ...num(ep.full), muted: !ep.full }), numeric: true, class: 'hidden sm:table-cell' },
    { id: 'time', label: 'Agent time', value: (ep) => ep.rebuildS + ep.runningS, cell: (ep) => ({ text: episodeTime(ep), nowrap: true }), numeric: true, class: 'hidden sm:table-cell' },
    { id: 'longest', label: 'Longest', value: (ep) => ep.longestS ?? -1, cell: (ep) => num(ep.longestS, seconds), numeric: true, class: 'hidden md:table-cell @5xl/main:hidden @[96rem]/main:table-cell' },
    { id: 'sources', label: 'Sources', value: episodeSources, class: 'hidden lg:table-cell @5xl/main:hidden @[96rem]/main:table-cell' },
  ]
}

export function episodeDetail(ep: RebuildEpisode): Detail {
  return {
    id: ep.id,
    title: `Base rebuild episode · ${shortDayTime(ep.firstMs)} UTC`,
    subtitle: episodeSources(ep),
    stats: [
      { label: 'Rebuilds', value: integer(ep.jobs.length) },
      { label: 'Full', value: integer(ep.full) },
      { label: 'Partial', value: integer(ep.partial) },
      { label: 'Still running', value: integer(ep.rebuilding) },
      { label: 'Agent time', value: episodeTime(ep) },
      { label: 'Span', value: episodeSpan(ep) },
    ],
    tables: [{ title: 'Rebuilds', columns: jobColumns(), rows: ep.jobs, pageSize: 10, onRowClick: jobDetail }],
    width: 'lg',
  }
}
