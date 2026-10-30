import { shortDayTime } from '@/components/charts/chart'
import type { Detail, DetailField } from '@/components/evidence/detail'
import { badge, link, num, text, type ColumnSpec, type FilterSpec } from '@/components/table/columns'
import { CACHE_LABELS, cacheClass, dockerJobUrl, stepLabel, stepShort, type DockerJob } from '@/domain/dockerBuilds'
import { seconds } from '@/domain/format'
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
