import type { Detail, DetailStat } from '@/components/evidence/detail'
import { badge, link, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import { integer, shortDate, value, type Rec } from '@/domain/format'
import {
  commitUrl,
  perfDelta,
  perfHistory,
  perfHistoryBuildLabel,
  perfImageDetail,
  perfModelDetail,
  perfModelFamilies,
  perfPointDetail,
  perfShortModel,
  perfStatusTone,
  perfValue,
  type PerfChange,
  type PerfDetail,
  type PerfHardwareRow,
} from '@/domain/perf'
import { hardwareColor, hardwareFamily, SERIES, type PaletteColor } from '@/lib/palette'

export interface PerfSeriesSpec {
  series: Rec[]
  unit: unknown
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

const TONE_LABEL: Partial<Record<string, string>> = { hard: 'Regressed', pass: 'Improved' }

function fromPerf(id: string, detail: PerfDetail): Detail {
  return { id, title: detail.title, subtitle: detail.subtitle, description: detail.description, fields: detail.fields, sources: detail.sources, width: 'md' }
}

export const pointDetail = (point: Rec, unit: unknown) => fromPerf(`perf-point:${point.build_number ?? point.date}:${point.value}`, perfPointDetail(point, unit))
export const imageDetail = (row: Rec) => fromPerf(`perf-image:${row.build_number}:${row.image}`, perfImageDetail(row))
export const modelDetail = (model: Rec) => fromPerf(`perf-model:${model.model}`, perfModelDetail(model))

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

/** classic openPerfHistory as an evidence sheet. */
export function historyDetail(model: Rec, config: Rec, metricName: string, block: Rec): PerfHistoryDetail {
  const history = perfHistory(model, config, metricName, block)
  const tone = perfStatusTone(block.status)
  const change: DetailStat = { label: 'Change', value: history.change, meta: 'vs previous nightly' }
  if (tone !== 'neutral') Object.assign(change, { tone, toneLabel: TONE_LABEL[tone] })
  return {
    id: `perf-history:${model.model}:${config.label ?? ''}:${metricName}`,
    title: history.title,
    subtitle: history.subtitle,
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
      },
    ],
    chart: { series: block.series || [], unit: block.unit, label: history.name, color: deviceColor(config.device) },
    width: 'xl',
  }
}

export const changeHistory = (change: PerfChange) => historyDetail(change.model, change.config, change.name, change.block)

export function changeColumns(): ColumnSpec<PerfChange>[] {
  return [
    {
      id: 'metric',
      label: 'Metric',
      value: (row) => `${row.label} ${row.model.model} ${row.config.label}`,
      cell: (row) => ({ text: row.label, sub: `${perfShortModel(row.model.model)} · ${row.config.label}`, detail: () => changeHistory(row) }),
      hideable: false,
    },
    { id: 'latest', label: 'Latest', numeric: true, value: (row) => Number(row.block.latest), cell: (row) => text(perfValue(row.block.latest, row.block.unit)) },
    { id: 'change', label: 'Change', numeric: true, value: (row) => row.deltaPct, cell: (row) => badge(perfDelta(row.block).label, row.tone) },
  ]
}

/** Regressed or improved metrics, each opening its history. */
export function changesDetail(id: string, title: string, subtitle: string, changes: PerfChange[]): Detail {
  return {
    id,
    title,
    subtitle,
    description: 'Each metric compares its latest nightly with the previous one. Select a metric to open its history.',
    tables: changes.length ? [{ title: `${integer(changes.length)} metrics`, columns: changeColumns(), rows: changes }] : [],
    empty: changes.length ? undefined : 'No metric changed beyond its threshold.',
    width: 'lg',
  }
}

type Family = ReturnType<typeof perfModelFamilies>[number]

/** The AMD MODELS drill-down: classic lists the families newest first. */
export function familiesDetail(perf: Rec, onModel: (model: string) => void): Detail {
  const rows = perfModelFamilies(perf)
  const columns: ColumnSpec<Family>[] = [
    { id: 'model', label: 'Model', value: (row) => row.label, cell: (row) => ({ text: row.label, onOpen: () => onModel(row.label) }), hideable: false },
    { id: 'nightlies', label: 'Nightlies', value: (row) => row.valueSummary, cell: (row) => text(row.valueSummary) },
    { id: 'build', label: 'Evidence', value: (row) => row.url || '', cell: (row) => (row.url ? link('Latest build', row.url) : text('Published source data')), sortable: false },
  ]
  return {
    id: 'perf-families',
    title: 'AMD model families',
    subtitle: `${integer(rows.length)} source-backed observations`,
    description: 'Select a model to open its performance histories.',
    tables: [{ columns, rows, pageSize: false }],
    width: 'lg',
  }
}

export function hardwareDetail(rows: PerfHardwareRow[], declared: string, onDevice: (device: string) => void): Detail {
  const count = (pick: (row: PerfHardwareRow) => number, label: string): ColumnSpec<PerfHardwareRow> => ({
    id: label.toLowerCase(),
    label,
    numeric: true,
    value: pick,
    cell: (row) => text(integer(pick(row))),
  })
  return {
    id: 'perf-hardware',
    title: 'AMD performance hardware',
    subtitle: 'ROCm only',
    description: 'Hardware declared by retained AMD workload records. Select a device to filter the page.',
    fields: [{ label: 'Declared devices', value: declared }],
    tables: [
      {
        columns: [
          { id: 'device', label: 'Hardware', value: (row) => row.label, cell: (row) => ({ text: row.label, onOpen: () => onDevice(row.id) }), hideable: false },
          count((row) => row.models, 'Models'),
          count((row) => row.configs, 'Configurations'),
          count((row) => row.metrics, 'Metrics'),
          count((row) => row.regressions, 'Regressions'),
          count((row) => row.improvements, 'Improvements'),
        ],
        rows,
        pageSize: false,
      },
    ],
    width: 'lg',
  }
}
