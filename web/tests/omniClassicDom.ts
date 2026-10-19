// Reads what the classic renderOmni draws (jsdom) and what the v2 Omni models
// describe into one comparable shape, including every drawer it opens.
import { detailSubtitle, detailTitle, visibleFields, type Cell, type DetailTable, type OmniDetail } from '@/domain/omni'
import type { Rec } from '@/domain/format'

export interface TableShape {
  headers: string[]
  rows: string[][]
  total: number
}
export interface DetailShape {
  title: string
  subtitle: string
  description: string
  fields: string[]
  sources: string[]
  strip: string[]
  panels: string[]
  tables: TableShape[]
  empty: string
}

const txt = (el: Element | null | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim()
const toneOf = (el: Element) => [...el.classList].find((c) => c.startsWith('is-') && c !== 'is-numeric' && c !== 'is-sticky-left') ?? ''

export function cellShape(td: Element): string {
  const a = td.querySelector('a')
  const badge = td.querySelector('.ops-badge')
  const tone = badge ? `{${toneOf(badge)}}` : ''
  return txt(td) + tone + (a ? ` <${a.getAttribute('href')}>` : '')
}

export function v2CellShape(cell: Cell): string {
  return cell.text.replace(/\s+/g, ' ').trim() + (cell.tone ? `{${cell.tone}}` : '') + (cell.href ? ` <${cell.href}>` : '')
}

function classicTotal(scope: Element, table: Element, rows: number): number {
  const count = scope.querySelector('.ops-browser-count')
  if (count) return Number(txt(count).match(/of ([\d,]+) rows/)![1]!.replace(/,/g, ''))
  const caption = txt(table.querySelector('caption'))
  const of = caption.match(/ of ([\d,]+)/)
  return of ? Number(of[1]!.replace(/,/g, '')) : rows
}

export function classicTableShape(scope: Element, table: Element): TableShape {
  const headers = [...table.querySelectorAll('thead th')].map(txt)
  const rows = [...table.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map(cellShape))
  return { headers, rows, total: classicTotal(scope, table, rows.length) }
}

export function topOverlay(): Element | null {
  const overlays = [...document.querySelectorAll('.ops-overlay')].filter((o) => !(o as HTMLElement).hidden)
  return overlays[overlays.length - 1] ?? null
}

export function classicDetailShape(overlay: Element): DetailShape {
  const body = overlay.querySelector('.ops-overlay-body')!
  const sources = [...body.querySelectorAll(':scope > div > .ops-source-actions a, :scope > .ops-source-actions a')].map(
    (a) => `${txt(a)} <${a.getAttribute('href')}>`,
  )
  return {
    title: txt(overlay.querySelector('.ops-overlay-title')),
    subtitle: txt(overlay.querySelector('.ops-overlay-subtitle')),
    description: txt(body.querySelector('.ops-detail-description')),
    fields: [...body.querySelectorAll('.ops-detail-fields .ops-detail-field')].map((f) => `${txt(f.querySelector('dt'))}=${txt(f.querySelector('dd'))}`),
    sources,
    strip: [...body.querySelectorAll('.ops-status-strip .ops-status-item')].map(
      (item) => `${txt(item.querySelector('.ops-stat-label'))}|${txt(item.querySelector('.ops-stat-value'))}|${txt(item.querySelector('.ops-stat-meta'))}`,
    ),
    panels: [...body.querySelectorAll('.ops-panel')].map((p) => `${txt(p.querySelector('.ops-panel-title'))}|${txt(p.querySelector('.ops-panel-meta'))}`),
    tables: [...body.querySelectorAll('table.ops-table')].map((t) => classicTableShape(body, t)),
    empty: [...body.querySelectorAll('.ops-empty')].map(txt).join(' / '),
  }
}

export function v2TableShape(table: DetailTable<Rec>, limit = Infinity): TableShape {
  return {
    headers: table.columns.map((c) => c.label),
    rows: table.rows.slice(0, limit).map((row) => table.columns.map((c) => v2CellShape(c.cell(row)))),
    total: table.rows.length,
  }
}

/** v2 detail in classic's shape; tables are cut to the rows classic shows on its first page. */
export function v2DetailShape(detail: OmniDetail, classic: DetailShape): DetailShape {
  // Classic dataTable() draws an empty table as a note, not a table.
  const tables = detail.tables.filter((t) => t.rows.length)
  const emptyBrowser = detail.tables.some((t) => !t.rows.length) ? 'No matching evidence. No matching observations.' : ''
  return {
    title: detailTitle(detail),
    subtitle: detailSubtitle(detail),
    description: (detail.description ?? '').replace(/\s+/g, ' ').trim(),
    fields: visibleFields(detail.fields).map((f) => `${f.label}=${f.text.replace(/\s+/g, ' ').trim()}`),
    sources: detail.sources.filter((s) => s && s.url).map((s) => `${s.label} <${s.url}>`),
    strip: (detail.strip ?? []).map((s) => `${s.label}|${s.value}|${s.meta}`),
    panels: detail.tables.filter((t) => t.title).map((t) => `${t.title}|${t.meta ?? ''}`),
    tables: tables.map((t, i) => v2TableShape(t, classic.tables[i]?.rows.length ?? Infinity)),
    empty: [detail.empty, emptyBrowser].filter(Boolean).join(' / '),
  }
}
