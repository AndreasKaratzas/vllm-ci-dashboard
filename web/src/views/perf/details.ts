import type { Detail, DetailStat } from '@/components/evidence/detail'
import type { Delta } from '@/components/stat/DeltaBadge.vue'
import { badge, link, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import { integer, shortDate, value, type Rec } from '@/domain/format'
import {
  commitUrl,
  perfConfigTitle,
  perfDelta,
  perfHardwareLabel,
  perfHistory,
  perfHistoryBuildLabel,
  perfImageDetail,
  perfLatestBuildLabel,
  perfModelDetail,
  perfModelFamilies,
  perfModelRows,
  perfModels,
  perfPointDetail,
  perfPointVerdict,
  perfShortModel,
  perfSparkRows,
  perfStatusTone,
  perfValue,
  type PerfChange,
  type PerfDetail,
  type PerfHardwareRow,
  type PerfNightly,
} from '@/domain/perf'
import { hardwareColor, hardwareFamily, SERIES, type PaletteColor } from '@/lib/palette'

export interface PerfSeriesSpec {
  series: Rec[]
  context: PointContext
  label: string
  color: PaletteColor
}

/** A metric history sheet: the evidence plus the chart drawn above its table. */
export interface PerfHistoryDetail extends Detail {
  chart: PerfSeriesSpec
}

export function isHistoryDetail(detail: Detail): detail is PerfHistoryDetail {
  return 'chart' in detail
}

export function deviceColor(device: unknown): PaletteColor {
  return hardwareFamily(device) ? hardwareColor(device) : SERIES.primary
}

export const deviceDots = (devices: unknown[]) => devices.map((device) => hardwareColor(device))

/** A rounded zero is no change, whichever side of zero it came from. */
export const deltaText = (label: string) => label.replace(/^[+-](0\.0%)$/, '$1')

/** A block's change as a delta badge whose verdict is the threshold status, not the sign. */
export function blockDelta(block: Rec): Delta {
  const delta = perfDelta(block)
  const pct = Math.round(Number(block.delta_pct) * 10) / 10
  return { value: Number.isFinite(pct) ? pct : 0, text: deltaText(delta.label), tone: delta.tone, label: delta.title ? 'vs the preceding nightly' : undefined }
}

const TONE_LABEL: Partial<Record<string, string>> = { hard: 'Regressed', pass: 'Improved' }

function fromPerf(id: string, detail: PerfDetail): Detail {
  return { id, title: detail.title, subtitle: detail.subtitle, description: detail.description, fields: detail.fields, sources: detail.sources, width: 'md' }
}

const FIELD_LABEL: Partial<Record<string, string>> = { value: 'Value', 'vllm commit': 'vLLM commit', image: 'Image' }

/** What a chart point belongs to: its model, configuration and metric block. */
export interface PointContext {
  model: Rec
  config: Rec
  block: Rec
  relative: boolean
}

export function pointDetail(point: Rec, context: PointContext, thresholds: Rec): Detail {
  const base = perfPointDetail(point, context.block.unit)
  const series = perfSparkRows(context.block.series)
  const index = series.indexOf(point)
  const previous = index > 0 ? series[index - 1] : null
  const stats: DetailStat[] = [{ label: 'Observed', value: point.date ? shortDate(point.date) : '—' }]
  if (previous) {
    const pct = Number(previous.value) ? ((Number(point.value) - Number(previous.value)) / Math.abs(Number(previous.value))) * 100 : NaN
    const verdict = perfStatusTone(perfPointVerdict(previous.value, point.value, context.block.direction, context.relative, thresholds))
    const change: DetailStat = {
      label: 'Change',
      value: Number.isFinite(pct) ? deltaText(`${pct > 0 ? '+' : ''}${pct.toFixed(1)}%`) : '—',
      meta: `vs ${perfHistoryBuildLabel(previous)} · ${perfValue(previous.value, context.block.unit)}`,
    }
    if (verdict !== 'neutral') Object.assign(change, { tone: verdict, toneLabel: TONE_LABEL[verdict] })
    stats.push(change)
  }
  if (context.config.device) stats.push({ label: 'Hardware', value: String(context.config.device).toUpperCase() })
  return {
    ...fromPerf(`perf-point:${point.build_number ?? point.date}:${point.value}`, base),
    description: [context.block.label, perfShortModel(context.model.model), context.config.device ? perfConfigTitle(context.config) : null].filter(Boolean).join(' · '),
    stats,
    fields: base.fields.map((field) => ({ label: FIELD_LABEL[field.label] ?? field.label, value: field.value })),
  }
}

export const imageDetail = (row: Rec) => fromPerf(`perf-image:${row.build_number}:${row.image}`, perfImageDetail(row))

export function modelDetail(model: Rec): Detail {
  const base = perfModelDetail(model)
  const row = perfModelRows([model])[0]!
  const latest = model.latest || {}
  return {
    ...fromPerf(`perf-model:${model.model}`, base),
    subtitle: [`${integer(model.nightly_count)} nightlies`, latest.build_number ? `latest build #${latest.build_number}` : null, latest.date ? shortDate(latest.date) : null]
      .filter(Boolean)
      .join(' · '),
    stats: [
      { label: 'Configurations', value: integer((model.perf_configs || []).length) },
      { label: 'Metrics', value: integer(row.performanceMetrics + row.accuracyMetrics), meta: `${integer(row.performanceMetrics)} performance · ${integer(row.accuracyMetrics)} accuracy` },
      { label: 'Regressions', value: integer(row.regressions), meta: 'vs previous nightly' },
      { label: 'Improvements', value: integer(row.improvements), meta: 'vs previous nightly' },
    ],
    fields: base.fields.map((field) => (field.label === 'Hardware' ? { ...field, value: perfHardwareLabel(model) } : field)),
  }
}

function historyColumns(unit: unknown): ColumnSpec<Rec>[] {
  return [
    { id: 'build', label: 'Nightly', value: (row) => Number(row.build_number || 0), cell: (row) => link(perfHistoryBuildLabel(row), row.build_url, true), hideable: false },
    { id: 'date', label: 'Observed', value: (row) => String(row.date || ''), cell: (row) => text(shortDate(row.date)) },
    {
      id: 'value',
      label: 'Value',
      numeric: true,
      value: (row) => (Number.isFinite(Number(row.value)) ? Number(row.value) : -Infinity),
      cell: (row) => text(perfValue(row.value, unit)),
    },
    {
      id: 'commit',
      label: 'vLLM commit',
      value: (row) => String(row.vllm_commit || ''),
      cell: (row) => (row.vllm_commit ? link(String(row.vllm_commit).slice(0, 7), commitUrl(row.vllm_commit), true) : text(null)),
    },
    {
      id: 'image',
      label: 'Image',
      value: (row) => String(row.image || ''),
      cell: (row): CellSpec => ({ text: value(row.image), mono: true, detail: () => imageDetail(row) }),
    },
  ]
}

export function historyDetail(model: Rec, config: Rec, metricName: string, block: Rec, thresholds: Rec, relative = true): PerfHistoryDetail {
  const history = perfHistory(model, config, metricName, block)
  const tone = perfStatusTone(block.status)
  const change: DetailStat = { label: 'Change', value: deltaText(history.change), meta: 'vs previous nightly' }
  if (tone !== 'neutral') Object.assign(change, { tone, toneLabel: TONE_LABEL[tone] })
  const context: PointContext = { model, config, block, relative }
  return {
    id: `perf-history:${model.model}:${config.label ?? ''}:${metricName}`,
    title: history.name,
    subtitle: `${model.model} · ${config.device ? perfConfigTitle(config) : config.label || 'Metric history'}`,
    description: `${history.name}${history.directionNote}`,
    stats: [{ label: 'Latest', value: history.latest }, { label: 'Previous', value: history.previous }, change, { label: 'Points', value: history.points }],
    sources: (model.latest || {}).build_url ? [{ label: 'Open latest perf build', url: model.latest.build_url }] : [],
    tables: [
      {
        title: history.series.length ? history.tableTitle : undefined,
        columns: historyColumns(block.unit),
        rows: history.series,
        pageSize: 15,
        emptyText: 'No matching observations.',
        onRowClick: (row: Rec) => pointDetail(row, context, thresholds),
      },
    ],
    chart: { series: block.series || [], context, label: history.name, color: deviceColor(config.device) },
    width: 'xl',
  }
}

export const changeHistory = (change: PerfChange, thresholds: Rec) =>
  historyDetail(change.model, change.config, change.name, change.block, thresholds, change.kind === 'performance')

export const changeWhere = (change: PerfChange) =>
  `${perfShortModel(change.model.model)} · ${change.kind === 'performance' ? perfConfigTitle(change.config) : 'Accuracy'}`

export function changeColumns(): ColumnSpec<PerfChange>[] {
  return [
    {
      id: 'metric',
      label: 'Metric',
      value: (row) => `${row.label} ${row.model.model} ${row.config.label}`,
      cell: (row) => ({ text: row.label, sub: changeWhere(row) }),
      hideable: false,
    },
    { id: 'latest', label: 'Latest', numeric: true, value: (row) => Number(row.block.latest), cell: (row) => text(perfValue(row.block.latest, row.block.unit)) },
    { id: 'change', label: 'Change', numeric: true, value: (row) => row.deltaPct, cell: (row) => badge(deltaText(perfDelta(row.block).label), row.tone) },
  ]
}

/** Regressed or improved metrics, each opening its history. */
export function changesDetail(id: string, title: string, subtitle: string, changes: PerfChange[], thresholds: Rec): Detail {
  const models = new Set(changes.map((change) => change.model.model)).size
  return {
    id,
    title,
    subtitle,
    description: changes.length ? `Across ${integer(models)} ${models === 1 ? 'model' : 'models'}, largest change first. Select a metric to open its history.` : undefined,
    tables: changes.length
      ? [{ title: `${integer(changes.length)} metrics`, columns: changeColumns(), rows: changes, onRowClick: (change: PerfChange) => changeHistory(change, thresholds) }]
      : [],
    empty: changes.length ? undefined : 'No metric changed beyond its threshold.',
    width: 'lg',
  }
}

type Family = ReturnType<typeof perfModelFamilies>[number] & { model: Rec }

export function familiesDetail(perf: Rec, onModel: (model: string) => void): Detail {
  const byName = new Map(perfModels(perf).map((model) => [model.model, model]))
  const rows: Family[] = perfModelFamilies(perf).map((family) => ({ ...family, model: byName.get(family.label) ?? {} }))
  const columns: ColumnSpec<Family>[] = [
    { id: 'model', label: 'Model', value: (row) => row.label, cell: (row) => ({ text: row.label, nowrap: true }), hideable: false },
    {
      id: 'hardware',
      label: 'Hardware',
      value: (row) => perfHardwareLabel(row.model),
      cell: (row) => ({ text: perfHardwareLabel(row.model), dots: deviceDots(row.model.devices || []), nowrap: true }),
    },
    { id: 'nightlies', label: 'Nightlies', value: (row) => row.valueSummary, cell: (row) => text(row.valueSummary) },
    { id: 'build', label: 'Latest build', value: (row) => row.url || '', cell: (row) => (row.url ? link(perfLatestBuildLabel(row.model), row.url, true) : text('Published source data')), sortable: false },
  ]
  return {
    id: 'perf-families',
    title: 'AMD model families',
    subtitle: `${integer(rows.length)} families with retained nightlies, newest first`,
    description: 'Select a model to open its performance histories.',
    tables: [{ columns, rows, pageSize: false, onRowClick: (row: Family) => onModel(row.label) }],
    width: 'lg',
  }
}

export function hardwareDetail(rows: PerfHardwareRow[], declared: string, accuracyChanges: number, onDevice: (device: string) => void): Detail {
  const count = (pick: (row: PerfHardwareRow) => number, label: string): ColumnSpec<PerfHardwareRow> => ({
    id: label.toLowerCase(),
    label,
    numeric: true,
    value: pick,
    cell: (row) => text(integer(pick(row))),
  })
  const sum = (pick: (row: PerfHardwareRow) => number) => rows.reduce((total, row) => total + pick(row), 0)
  return {
    id: 'perf-hardware',
    title: 'AMD performance hardware',
    subtitle: `${declared} · ROCm only`,
    description:
      'Hardware declared by retained AMD workload records. Select a device to filter the page.' +
      (accuracyChanges ? ` Accuracy tasks are not tied to a device, so their ${integer(accuracyChanges)} changed ${accuracyChanges === 1 ? 'metric is' : 'metrics are'} not counted here.` : ''),
    stats: [
      { label: 'Devices', value: integer(rows.length) },
      { label: 'Configurations', value: integer(sum((row) => row.configs)) },
      { label: 'Regressions', value: integer(sum((row) => row.regressions)), meta: 'performance metrics' },
      { label: 'Improvements', value: integer(sum((row) => row.improvements)), meta: 'performance metrics' },
    ],
    tables: [
      {
        columns: [
          { id: 'device', label: 'Hardware', value: (row) => row.label, cell: (row) => ({ text: row.label, dots: [hardwareColor(row.id)] }), hideable: false },
          count((row) => row.models, 'Models'),
          count((row) => row.configs, 'Configurations'),
          count((row) => row.metrics, 'Metrics'),
          count((row) => row.regressions, 'Regressions'),
          count((row) => row.improvements, 'Improvements'),
        ],
        rows,
        pageSize: false,
        onRowClick: (row: PerfHardwareRow) => onDevice(row.id),
      },
    ],
    width: 'lg',
  }
}

export function nightliesDetail(nightlies: PerfNightly[], subtitle: string): Detail {
  const count = (pick: (row: PerfNightly) => number, id: string, label: string): ColumnSpec<PerfNightly> => ({
    id,
    label,
    numeric: true,
    value: pick,
    cell: (row) => text(integer(pick(row))),
  })
  return {
    id: 'perf-nightlies',
    title: 'Perf-eval nightlies',
    subtitle,
    description: 'Each retained nightly with the models and metric points it reported. Regressed and improved count the metrics whose latest observation as of that nightly moved past the threshold, the same rule as the page counts.',
    tables: [
      {
        columns: [
          { id: 'build', label: 'Nightly', value: (row) => row.buildNumber ?? 0, cell: (row) => link(row.buildNumber ? `#${row.buildNumber}` : row.key, row.url, true), hideable: false },
          { id: 'date', label: 'Date', value: (row) => row.date, cell: (row) => text(shortDate(row.date)) },
          count((row) => row.models, 'models', 'Models'),
          count((row) => row.points, 'points', 'Metric points'),
          count((row) => row.regressions, 'regressions', 'Regressed'),
          count((row) => row.improvements, 'improvements', 'Improved'),
        ],
        rows: [...nightlies].reverse(),
        pageSize: 15,
      },
    ],
    width: 'lg',
  }
}
