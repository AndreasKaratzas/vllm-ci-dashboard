// Perf Eval page decisions over data/vllm/perf_eval/perf_eval.json.
import { integer, shortDate, shortSha, value, type Rec, type Tone } from './format'

export const PERF_SOURCE = 'data/vllm/perf_eval/perf_eval.json'
export const PERF_PIPELINE_URL = 'https://buildkite.com/vllm/perf-eval'
export const PERF_VIEWS = ['performance', 'accuracy'] as const
export type PerfView = (typeof PERF_VIEWS)[number]

export interface PerfState {
  view: PerfView
  model: string
  device: string
}

/** The route state: view is validated, model/device take any non-empty value. */
export function perfState(query: { view?: unknown; model?: unknown; device?: unknown }): PerfState {
  const pick = (v: unknown) => (typeof v === 'string' ? v : Array.isArray(v) && typeof v[0] === 'string' ? v[0] : null)
  const view = pick(query.view)
  const model = pick(query.model)
  const device = pick(query.device)
  return {
    view: view && (PERF_VIEWS as readonly string[]).includes(view) ? (view as PerfView) : 'performance',
    model: model || 'all',
    device: device || 'all',
  }
}

/** The route query for a state; defaults are dropped. */
export function perfQuery(state: PerfState): Record<string, string> {
  const query: Record<string, string> = {}
  if (state.view !== 'performance') query.view = state.view
  if (state.model && state.model !== 'all') query.model = state.model
  if (state.device && state.device !== 'all') query.device = state.device
  return query
}

export function perfValue(raw: unknown, unit?: unknown): string {
  const number = Number(raw)
  if (!Number.isFinite(number)) return '-'
  if (unit === 'ratio') return (number * 100).toFixed(1) + '%'
  if (unit === 's') return number < 1 ? Math.round(number * 1000) + ' ms' : number.toFixed(2) + ' s'
  let rendered: string
  if (Math.abs(number) >= 1000) rendered = Math.round(number).toLocaleString()
  else if (Math.abs(number) >= 1) rendered = number.toFixed(1)
  else rendered = number.toFixed(3)
  return unit ? rendered + ' ' + unit : rendered
}

export function perfStatusTone(status: unknown): Tone {
  return status === 'good' ? 'pass' : status === 'bad' ? 'hard' : 'neutral'
}

export interface PerfDelta {
  label: string
  tone: Tone
  title: string | null
}

/** A block's change versus the previous nightly: text, tone and title. */
export function perfDelta(block: Rec): PerfDelta {
  if (block.previous === null || block.previous === undefined) return { label: 'First nightly', tone: 'neutral', title: null }
  const delta = Number(block.delta_pct)
  if (!Number.isFinite(delta)) return { label: 'No comparison', tone: 'neutral', title: null }
  const label = (delta > 0 ? '+' : '') + delta.toFixed(1) + '%'
  return { label, tone: perfStatusTone(block.status), title: 'Change from the preceding nightly' }
}

export function perfDirectionLabel(block: Rec): string {
  return block.direction === 'lower' ? 'Lower is better' : 'Higher is better'
}

export function accuracyStatusLabel(status: unknown): string {
  return status === 'good' ? 'improved' : status === 'bad' ? 'regressed' : 'within band'
}

export function miDevice(device: unknown): boolean {
  return /^mi\d{3,4}[ax]?$/.test(String(device || '').toLowerCase())
}

export function perfModels(perf: Rec | null | undefined): Rec[] {
  return Array.isArray((perf || {}).models) ? perf!.models : []
}

/** Any non-MI or GPU-less record withholds the whole page rather than mixing hardware. */
export function perfDataUnavailable(perf: Rec): boolean {
  const models = perfModels(perf)
  const summary = perf.summary || {}
  return (
    models.some(
      (model) =>
        !Array.isArray(model.devices) ||
        !model.devices.length ||
        model.devices.some((device: unknown) => !miDevice(device)) ||
        (model.perf_configs || []).some((config: Rec) => !miDevice(config.device) || config.no_gpu === true),
    ) || (summary.amd_devices || []).some((device: unknown) => !miDevice(device))
  )
}

export function perfPipelineUrl(perf: Rec): string {
  return (perf.pipeline || {}).url || PERF_PIPELINE_URL
}

export interface PerfKpis {
  models: string
  nightlies: string
  points: string
  pointsRaw: number
  pointsMeta: string
  hardware: string
  hardwareDetail: string
}

export function perfKpis(perf: Rec): PerfKpis {
  const summary = perf.summary || {}
  const models = perfModels(perf)
  const points = Number(summary.perf_points || 0) + Number(summary.accuracy_points || 0)
  return {
    models: integer(summary.models !== undefined ? summary.models : models.length),
    nightlies: integer(summary.nightlies),
    points: integer(points),
    pointsRaw: points,
    pointsMeta: integer(summary.perf_points) + ' performance - ' + integer(summary.accuracy_points) + ' accuracy',
    hardware: (summary.amd_devices || []).map((device: unknown) => String(device).toUpperCase()).join(' / ') || '-',
    hardwareDetail: (summary.amd_devices || []).join(', ') || '-',
  }
}

/** The model families drill-down lists the payload order reversed (newest family first). */
export function perfModelFamilies(perf: Rec): { label: string; valueSummary: string; url: string | undefined }[] {
  return perfModels(perf)
    .map((model) => ({ label: model.model, valueSummary: integer(model.nightly_count) + ' nightlies', url: (model.latest || {}).build_url }))
    .reverse()
}

export function perfDevices(perf: Rec): { id: string; label: string }[] {
  const summary = perf.summary || {}
  const devices: unknown[] = summary.amd_devices || Array.from(new Set(perfModels(perf).flatMap((model) => model.devices || [])))
  return devices.map((device) => ({ id: String(device).toLowerCase(), label: String(device).toUpperCase() }))
}

export function filterPerfModels(models: Rec[], state: Pick<PerfState, 'model' | 'device'>): Rec[] {
  return models.filter((model) => {
    if (state.model !== 'all' && model.model !== state.model) return false
    if (state.device !== 'all' && !(model.devices || []).some((device: unknown) => String(device).toLowerCase() === state.device))
      return false
    return true
  })
}

export interface PerfModelRow {
  model: Rec
  performanceMetrics: number
  accuracyMetrics: number
  regressions: number
  improvements: number
}

export function perfModelRows(models: Rec[]): PerfModelRow[] {
  return models.map((model) => {
    const performanceMetrics: Rec[] = (model.perf_configs || []).flatMap((config: Rec) => Object.values(config.metrics || {}))
    const accuracyMetrics: Rec[] = model.accuracy_tasks || []
    const all = performanceMetrics.concat(accuracyMetrics)
    return {
      model,
      performanceMetrics: performanceMetrics.length,
      accuracyMetrics: accuracyMetrics.length,
      regressions: all.filter((metric) => metric.status === 'bad').length,
      improvements: all.filter((metric) => metric.status === 'good').length,
    }
  })
}

export function perfHardwareLabel(model: Rec): string {
  return (model.devices || []).map((device: unknown) => String(device).toUpperCase()).join(' / ') || '-'
}

export function perfLatestBuildLabel(model: Rec): string {
  const latest = model.latest || {}
  return latest.build_number ? '#' + latest.build_number : 'Build'
}

export function perfModelConfigs(model: Rec, device: string): Rec[] {
  return (model.perf_configs || []).filter(
    (config: Rec) => device === 'all' || String(config.device || '').toLowerCase() === device,
  )
}

export function perfConfigLabel(config: Rec): string {
  return config.label || 'Performance configuration'
}

export function perfConfigMeta(config: Rec): string {
  return ['TP ' + value(config.tp), value(config.precision)].join(' - ')
}

export const PREFERRED_METRICS = [
  'tput_per_gpu', 'output_tput_per_gpu', 'input_tput_per_gpu', 'mean_ttft', 'p99_ttft', 'mean_tpot', 'mean_itl', 'mean_intvty',
]

export function sortedMetricNames(metrics: Rec | null | undefined): string[] {
  return Object.keys(metrics || {}).sort((a, b) => {
    const ai = PREFERRED_METRICS.indexOf(a)
    const bi = PREFERRED_METRICS.indexOf(b)
    return (ai < 0 ? 99 : ai) - (bi < 0 ? 99 : bi) || a.localeCompare(b)
  })
}

export function accuracyRows(models: Rec[]): { model: Rec; task: Rec }[] {
  const rows: { model: Rec; task: Rec }[] = []
  models.forEach((model) => {
    ;(model.accuracy_tasks || []).forEach((task: Rec) => rows.push({ model, task }))
  })
  return rows
}

/** The history block of an accuracy row. */
export function accuracyBlock(task: Rec): Rec {
  return { ...task, label: task.task + ' - ' + task.metric, unit: 'ratio', direction: task.direction || 'higher' }
}

export interface PerfModelProvenance {
  commit: { label: string; url: string } | null
  build: { label: string; url: string | undefined } | null
  date: string | null
  image: string | null
}

export function commitUrl(sha: unknown): string {
  return 'https://github.com/vllm-project/vllm/commit/' + sha
}

export function perfModelProvenance(model: Rec): PerfModelProvenance {
  const latest = model.latest || {}
  return {
    commit: latest.vllm_commit ? { label: 'commit ' + String(latest.vllm_commit).slice(0, 7), url: commitUrl(latest.vllm_commit) } : null,
    build:
      latest.build_number !== null && latest.build_number !== undefined
        ? { label: 'build #' + latest.build_number, url: latest.build_url }
        : null,
    date: latest.date ? shortDate(latest.date) : null,
    image: latest.image || null,
  }
}

export interface DetailField {
  label: string
  value: string
}
export interface DetailSource {
  label: string
  url: string
}
export interface PerfDetail {
  title: string
  subtitle: string
  description?: string
  fields: DetailField[]
  sources: DetailSource[]
}

function present(fields: { label: string; value: unknown }[]): DetailField[] {
  return fields
    .filter((field) => !(field.value === null || field.value === undefined || field.value === ''))
    .map((field) => ({ label: field.label, value: String(field.value) }))
}

function sources(rows: (DetailSource | null | { label: string; url: unknown })[]): DetailSource[] {
  return rows.filter((source): source is DetailSource => Boolean(source && source.url))
}

/** The model title drawer. */
export function perfModelDetail(model: Rec): PerfDetail {
  const latest = model.latest || {}
  return {
    title: model.model,
    subtitle: 'Performance and evaluation model history',
    fields: present([
      { label: 'Nightlies', value: integer(model.nightly_count) },
      { label: 'Hardware', value: (model.devices || []).join(', ') },
      { label: 'Latest commit', value: latest.vllm_commit ? shortSha(latest.vllm_commit) : null },
      { label: 'Latest image', value: latest.image },
    ]),
    sources: sources([
      latest.build_url ? { label: 'Open latest perf build', url: latest.build_url } : null,
      latest.vllm_commit ? { label: 'Open vLLM commit', url: commitUrl(latest.vllm_commit) } : null,
    ]),
  }
}

/** The runtime-image drawer opened from a history row. */
export function perfImageDetail(row: Rec): PerfDetail {
  return {
    title: 'Runtime image',
    subtitle: 'Source evidence and retained history',
    fields: present([
      { label: 'Image', value: value(row.image) },
      { label: 'Build', value: row.build_number ? '#' + row.build_number : null },
    ]),
    sources: row.build_url ? [{ label: 'Open producing build', url: row.build_url }] : [],
  }
}

export interface PerfHistory {
  title: string
  subtitle: string
  name: string
  directionNote: string
  latest: string
  previous: string
  change: string
  changeTone: Tone
  points: string
  series: Rec[]
  tableTitle: string
}

/** A metric history: rows newest first, summary of the block. */
export function perfHistory(model: Rec, config: Rec, metricName: string, block: Rec): PerfHistory {
  const series = (block.series || [])
    .slice()
    .sort((a: Rec, b: Rec) => String(b.date || '').localeCompare(String(a.date || '')))
  return {
    title: model.model + ' - ' + (config.label || 'Metric history'),
    subtitle: block.label || metricName,
    name: block.label || metricName,
    directionNote:
      ' - ' + (block.direction === 'lower' ? 'lower is better' : 'higher is better') + '. Every point links to its source perf-eval build.',
    latest: perfValue(block.latest, block.unit),
    previous: perfValue(block.previous, block.unit),
    change: perfDelta(block).label,
    changeTone: perfStatusTone(block.status),
    points: integer(series.length),
    series,
    tableTitle: integer(series.length) + ' perf-eval nightly observations',
  }
}

export function perfHistoryBuildLabel(row: Rec): string {
  return row.build_number ? '#' + row.build_number : 'Build'
}

/** Chartable points: only finite values; no chart below two. */
export function perfSparkRows(series: Rec[] | null | undefined): Rec[] {
  return (series || []).filter((point) => Number.isFinite(Number(point.value)))
}

/** One chart point as evidence. */
export function perfPointDetail(point: Rec, unit: unknown): PerfDetail {
  const label = point.build_number ? 'Build #' + point.build_number : shortDate(point.date)
  const details: Rec = { value: perfValue(point.value, unit), vllm_commit: point.vllm_commit, image: point.image }
  return {
    title: label || (point.date ? shortDate(point.date) : 'Observation'),
    subtitle: point.build_url ? 'Source-backed chart observation' : 'Retained chart observation',
    fields: present(Object.entries(details).map(([key, field]) => ({ label: key.replace(/_/g, ' '), value: field }))),
    sources: [point.build_url ? { label: 'Open exact source', url: String(point.build_url) } : { label: 'Open published source data', url: PERF_SOURCE }],
  }
}

export interface PerfTile {
  name: string
  block: Rec
  label: string
  delta: PerfDelta
  value: string
  direction: string
  tone: Tone
}

export interface PerfConfigSection {
  config: Rec
  label: string
  meta: string
  tiles: PerfTile[]
}

export interface PerfModelSection {
  model: Rec
  devices: string[]
  provenance: PerfModelProvenance
  nightlies: string
  configs: PerfConfigSection[]
}

/** A model's configurations and metric tiles, as data. */
export function perfModelSection(model: Rec, device: string): PerfModelSection {
  return {
    model,
    devices: (model.devices || []).map((d: unknown) => String(d).toUpperCase()),
    provenance: perfModelProvenance(model),
    nightlies: integer(model.nightly_count) + ' nightlies',
    configs: perfModelConfigs(model, device).map((config) => ({
      config,
      label: perfConfigLabel(config),
      meta: perfConfigMeta(config),
      tiles: sortedMetricNames(config.metrics).map((name) => {
        const block: Rec = config.metrics[name]
        return {
          name,
          block,
          label: block.label || name,
          delta: perfDelta(block),
          value: perfValue(block.latest, block.unit),
          direction: perfDirectionLabel(block),
          tone: perfStatusTone(block.status),
        }
      }),
    })),
  }
}

export type PerfPage =
  | { kind: 'unavailable' }
  | ({ kind: 'empty' } & PerfPageCommon)
  | ({ kind: 'summary'; rows: PerfModelRow[] } & PerfPageCommon)
  | ({ kind: 'accuracy'; rows: { model: Rec; task: Rec }[] } & PerfPageCommon)
  | ({ kind: 'performance'; sections: PerfModelSection[] } & PerfPageCommon)

export interface PerfPageCommon {
  kpis: PerfKpis
  devices: { id: string; label: string }[]
  modelNames: string[]
  showBack: boolean
}

/** Which body the page shows for a state. */
export function perfPage(perf: Rec, state: PerfState): PerfPage {
  if (perfDataUnavailable(perf)) return { kind: 'unavailable' }
  const models = perfModels(perf)
  const common: PerfPageCommon = {
    kpis: perfKpis(perf),
    devices: perfDevices(perf),
    modelNames: models.map((model) => model.model),
    showBack: state.model !== 'all',
  }
  const filtered = filterPerfModels(models, state)
  if (!filtered.length) return { kind: 'empty', ...common }
  if (state.model === 'all') return { kind: 'summary', rows: perfModelRows(filtered), ...common }
  if (state.view === 'accuracy') return { kind: 'accuracy', rows: accuracyRows(filtered), ...common }
  return { kind: 'performance', sections: filtered.map((model) => perfModelSection(model, state.device)), ...common }
}

// Presentation helpers.

/** The model name without its organization prefix. */
export function perfShortModel(name: unknown): string {
  const text = String(name ?? '')
  return text.slice(text.lastIndexOf('/') + 1) || text
}

/** A configuration title without the collector's "?" placeholders for unrecorded lengths. */
export function perfConfigTitle(config: Rec): string {
  const known = perfConfigLabel(config)
    .replace(/\?\s*in\s*\/\s*/, '')
    .replace(/\s*\/\s*\?\s*out/, '')
    .replace(/^\?\s*(in|out)\s*/, '')
  return known.replace(/^@\s*conc\b/, 'Concurrency')
}

/** Models matching the state, each keeping only the configurations on the selected device. */
export function perfScopedModels(models: Rec[], state: Pick<PerfState, 'model' | 'device'>): Rec[] {
  return filterPerfModels(models, state).map((model) =>
    state.device === 'all' ? model : { ...model, perf_configs: perfModelConfigs(model, state.device) },
  )
}

/** The same models with their accuracy tasks only. */
export function perfAccuracyOnly(models: Rec[]): Rec[] {
  return models.map((model) => ({ ...model, perf_configs: [] }))
}

export type PerfVerdict = 'good' | 'bad' | 'neutral'

/** The collector's red/green rule for one nightly against the one before it. */
export function perfPointVerdict(previous: unknown, latest: unknown, direction: unknown, relative: boolean, thresholds: Rec): PerfVerdict {
  const before = Number(previous)
  const after = Number(latest)
  if (previous === null || previous === undefined || !Number.isFinite(before) || !Number.isFinite(after)) return 'neutral'
  const delta = after - before
  const moved = relative ? before !== 0 && Math.abs(delta / before) >= Number(thresholds.perf_rel) : Math.abs(delta) >= Number(thresholds.accuracy_abs)
  if (!moved) return 'neutral'
  return (direction === 'lower' ? delta < 0 : delta > 0) ? 'good' : 'bad'
}

export interface PerfNightly {
  key: string
  buildNumber: number | null
  url: string | undefined
  date: string
  models: number
  points: number
  /** As of this nightly, metrics whose latest observation so far moved past the threshold versus the one before it. */
  regressions: number
  improvements: number
}

function eachSeries(model: Rec, visit: (series: Rec[], direction: unknown, relative: boolean) => void) {
  ;(model.perf_configs || []).forEach((config: Rec) =>
    Object.values(config.metrics || {}).forEach((block) => visit((block as Rec).series || [], (block as Rec).direction, true)),
  )
  ;(model.accuracy_tasks || []).forEach((task: Rec) => visit(task.series || [], task.direction || 'higher', false))
}

/**
 * Every retained nightly, oldest first, with the models and metric points it reported. Given thresholds, each
 * nightly also carries the regressed and improved metric counts as of that nightly, so the newest nightly
 * matches the latest-status counts of perfModelRows.
 */
export function perfNightlyActivity(models: Rec[], thresholds?: Rec): PerfNightly[] {
  type Entry = Omit<PerfNightly, 'key' | 'models'> & { models: Set<string>; verdicts: [number, PerfVerdict][] }
  const nightlies = new Map<string, Entry>()
  const keyOf = (point: Rec) => (point.build_number !== null && point.build_number !== undefined ? `#${point.build_number}` : String(point.date || ''))
  let metric = 0
  models.forEach((model) =>
    eachSeries(model, (series, direction, relative) => {
      metric += 1
      series.forEach((point, index) => {
        const key = keyOf(point)
        if (!key) return
        const entry: Entry = nightlies.get(key) ?? {
          buildNumber: Number(point.build_number) || null,
          url: undefined,
          date: '',
          models: new Set<string>(),
          points: 0,
          regressions: 0,
          improvements: 0,
          verdicts: [],
        }
        entry.models.add(model.model)
        entry.points += 1
        entry.url ??= point.build_url || undefined
        if (String(point.date || '') > entry.date) entry.date = String(point.date)
        if (thresholds) entry.verdicts.push([metric, index > 0 ? perfPointVerdict(series[index - 1]!.value, point.value, direction, relative, thresholds) : 'neutral'])
        nightlies.set(key, entry)
      })
    }),
  )
  const sorted = Array.from(nightlies, ([key, entry]) => ({ key, entry })).sort(
    (a, b) => a.entry.date.localeCompare(b.entry.date) || (a.entry.buildNumber ?? 0) - (b.entry.buildNumber ?? 0),
  )
  const state = new Map<number, PerfVerdict>()
  return sorted.map(({ key, entry: { verdicts, models: reported, ...entry } }) => {
    verdicts.forEach(([id, verdict]) => state.set(id, verdict))
    const current = Array.from(state.values())
    return {
      ...entry,
      key,
      models: reported.size,
      regressions: current.filter((verdict) => verdict === 'bad').length,
      improvements: current.filter((verdict) => verdict === 'good').length,
    }
  })
}

/** The newest perf-eval build any model reports: upstream activity, not collection age. */
export function perfLatestBuild(models: Rec[]): { number: number; url: string | undefined; date: string | undefined } | null {
  let best: Rec | null = null
  models.forEach((model) => {
    const latest = model.latest || {}
    if (Number.isFinite(Number(latest.build_number)) && (!best || Number(latest.build_number) > Number(best.build_number))) best = latest
  })
  const latest = best as Rec | null
  return latest ? { number: Number(latest.build_number), url: latest.build_url, date: latest.date } : null
}

export interface PerfChange {
  kind: 'performance' | 'accuracy'
  model: Rec
  config: Rec
  name: string
  block: Rec
  label: string
  tone: Tone
  deltaPct: number
}

/** Metrics whose latest nightly regressed or improved (as counted by perfModelRows), largest change first. */
export function perfChanges(models: Rec[]): PerfChange[] {
  const changes: PerfChange[] = []
  models.forEach((model) => {
    ;(model.perf_configs || []).forEach((config: Rec) =>
      sortedMetricNames(config.metrics).forEach((name) => {
        const block: Rec = config.metrics[name]
        changes.push({ kind: 'performance', model, config, name, block, label: block.label || name, tone: perfStatusTone(block.status), deltaPct: Number(block.delta_pct) })
      }),
    )
    ;(model.accuracy_tasks || []).forEach((task: Rec) => {
      const block = accuracyBlock(task)
      changes.push({ kind: 'accuracy', model, config: { label: 'Accuracy' }, name: task.metric, block, label: block.label, tone: perfStatusTone(task.status), deltaPct: Number(task.delta_pct) })
    })
  })
  const size = (change: PerfChange) => (Number.isFinite(change.deltaPct) ? Math.abs(change.deltaPct) : -1)
  return changes.filter((change) => change.tone !== 'neutral').sort((a, b) => size(b) - size(a))
}

/** The lead metric of a model's first configuration on the device, as a trend. */
export function perfModelTrend(model: Rec, device: string): { values: number[]; label: string; device: string } | null {
  for (const config of perfModelConfigs(model, device)) {
    const name = sortedMetricNames(config.metrics)[0]
    if (!name) continue
    const block: Rec = config.metrics[name]
    const values = perfSparkRows(block.series).map((point) => Number(point.value))
    if (values.length < 2) continue
    return { values, label: `${block.label || name}, ${perfConfigTitle(config)}`, device: String(config.device || '') }
  }
  return null
}

export interface PerfHardwareRow {
  id: string
  label: string
  models: number
  configs: number
  metrics: number
  regressions: number
  improvements: number
}

/** Per AMD device: the models, configurations and performance metrics that ran on it. */
export function perfHardwareRows(perf: Rec): PerfHardwareRow[] {
  return perfDevices(perf).map((device) => {
    const configs = perfModels(perf).flatMap((model) => perfModelConfigs(model, device.id).map((config) => ({ model, config })))
    const metrics: Rec[] = configs.flatMap(({ config }) => Object.values(config.metrics || {}))
    return {
      ...device,
      models: new Set(configs.map(({ model }) => model.model)).size,
      configs: configs.length,
      metrics: metrics.length,
      regressions: metrics.filter((metric) => metric.status === 'bad').length,
      improvements: metrics.filter((metric) => metric.status === 'good').length,
    }
  })
}
