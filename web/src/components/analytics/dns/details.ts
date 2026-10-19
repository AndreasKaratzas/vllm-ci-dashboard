import type { Detail, DetailStat } from '@/components/evidence/detail'
import { link, mono, num, text, type ColumnSpec } from '@/components/table/columns'
import {
  DNS_FALLBACK_ASSET,
  queueDnsDisplayCount,
  queueDnsEvidenceForNode,
  queueDnsEvidenceSearchText,
  queueDnsEvidenceUrl,
  queueDnsNodeEvidence,
  queueDnsOutcomePresentation,
  queueDnsSignatureLabels,
  queueDnsTargetLabels,
  type DnsNodeBar,
  type DnsPresentation,
  type DnsQueueCard,
} from '@/domain/dns'
import { integer, shortDate, value, type Rec } from '@/domain/format'

/** What every DNS evidence builder needs: the payload, the selected window's presentation, and where it came from. */
export interface DnsContext {
  payload: Rec
  view: DnsPresentation
}

const source = (ctx: DnsContext) => [{ label: 'Published DNS observations', url: ctx.payload.__sourceAsset || DNS_FALLBACK_ASSET }]

const DESCRIPTION =
  'One row per Buildkite job attempt whose log carries a DNS resolver signature. DNS observation is not the job outcome: the outcome is the final job state after the observation, and none of them establishes DNS as the cause.'

function evidenceColumns(withPlace: boolean): ColumnSpec<Rec>[] {
  const place: ColumnSpec<Rec>[] = withPlace
    ? [
        { id: 'queue', label: 'Queue', value: (row) => value(row.queue), cell: (row) => mono(row.queue) },
        { id: 'node', label: 'Physical node', value: (row) => value(row.node, '(unidentified)'), cell: (row) => mono(row.node || '(unidentified)') },
      ]
    : []
  return [
    {
      id: 'outcome',
      label: 'Job outcome',
      value: (row) => queueDnsOutcomePresentation(row.state).label,
      cell: (row) => {
        const outcome = queueDnsOutcomePresentation(row.state)
        return { text: outcome.label, tone: outcome.tone }
      },
      hideable: false,
    },
    { id: 'log', label: 'Evidence', value: (row) => queueDnsEvidenceUrl(row), cell: (row) => link('Exact log', queueDnsEvidenceUrl(row)), sortable: false },
    ...place,
    {
      id: 'observed',
      label: 'Observed',
      value: (row) => String(row.first_at || ''),
      cell: (row) => text(shortDate(row.first_at), row.last_at && row.last_at !== row.first_at ? `until ${shortDate(row.last_at)}` : undefined),
    },
    { id: 'build', label: 'Build', value: (row) => Number(row.build_number) || 0, cell: (row) => mono(`${value(row.pipeline)} #${value(row.build_number)}`) },
    { id: 'hardware', label: 'Hardware', value: (row) => value(row.hardware) },
    { id: 'episodes', label: 'Episodes', value: (row) => Number(row.episodes) || 0, cell: (row) => num(Number(row.episodes) || 0), numeric: true },
    { id: 'matches', label: 'Raw matches', value: (row) => Number(row.match_count) || 0, cell: (row) => num(Number(row.match_count) || 0), numeric: true },
    { id: 'targets', label: 'Targets', value: (row) => value(queueDnsTargetLabels(row).join(', ')) },
    { id: 'signatures', label: 'DNS signatures', value: (row) => value(queueDnsSignatureLabels(row).join(', ')) },
    { id: 'basis', label: 'Time basis', value: (row) => value(String(row.time_basis || '').replace(/_/g, ' ')) },
  ]
}

const STAT_LABELS = ['Affected jobs', 'DNS episodes', 'Hugging Face affected jobs', 'Exact evidence shown']

/** The exact Buildkite logs behind one node bar. */
export function nodeDetail(ctx: DnsContext, card: DnsQueueCard, bar: DnsNodeBar): Detail {
  const drawer = queueDnsNodeEvidence(ctx.payload, ctx.view.windowId, card.queue, bar.resolved, ctx.view.coverage)
  const shown = drawer.fields.filter((field) => field.value !== null && field.value !== '')
  const stats: DetailStat[] = shown.filter((field) => STAT_LABELS.includes(field.label)).map((field) => ({ label: field.label, value: String(field.value) }))
  return {
    id: `dns-node:${card.queue.queue}:${bar.node.node}`,
    title: drawer.title,
    subtitle: drawer.subtitle,
    description: DESCRIPTION,
    notes: drawer.note ? [{ tone: drawer.note.tone, text: drawer.note.text }] : [],
    stats,
    fields: shown.filter((field) => !STAT_LABELS.includes(field.label)),
    sources: source(ctx),
    tables: [
      {
        title: 'Exact Buildkite log evidence',
        meta: drawer.tableMeta,
        columns: evidenceColumns(false),
        rows: drawer.rows,
        searchText: queueDnsEvidenceSearchText,
        emptyText: 'No exact log links were retained for this node.',
        pageSize: 30,
      },
    ],
    width: 'xl',
  }
}

function nodeColumns(ctx: DnsContext, withQueue: boolean): ColumnSpec<{ card: DnsQueueCard; bar: DnsNodeBar }>[] {
  const count = (raw: number) => queueDnsDisplayCount(raw, ctx.view.coverage)
  return [
    ...(withQueue
      ? [{ id: 'queue', label: 'Queue', value: (r: { card: DnsQueueCard }) => r.card.queue.queue, cell: (r: { card: DnsQueueCard }) => mono(r.card.queue.queue) }]
      : []),
    {
      id: 'node',
      label: 'Physical node',
      value: (r) => r.bar.node.node,
      cell: (r) => ({ text: r.bar.node.node, mono: true, detail: () => nodeDetail(ctx, r.card, r.bar) }),
      hideable: false,
    },
    { id: 'jobs', label: 'Jobs', value: (r) => r.bar.node.affectedJobs, cell: (r) => text(r.bar.renderedCount), numeric: true },
    { id: 'outcomes', label: 'Final outcomes', value: (r) => r.bar.outcomeText, sortable: false },
    { id: 'episodes', label: 'Episodes', value: (r) => r.bar.node.episodes, cell: (r) => text(count(r.bar.node.episodes)), numeric: true },
    { id: 'hf', label: 'Hugging Face jobs', value: (r) => r.bar.node.huggingfaceAffectedJobs, cell: (r) => text(count(r.bar.node.huggingfaceAffectedJobs)), numeric: true },
  ]
}

/** One affected queue: its totals and every node, each drilling into its exact logs. */
export function queueDetail(ctx: DnsContext, card: DnsQueueCard): Detail {
  const count = (raw: number) => queueDnsDisplayCount(raw, ctx.view.coverage)
  return {
    id: `dns-queue:${card.queue.queue}`,
    title: card.queue.queue,
    subtitle: `DNS observations by physical node, ${ctx.view.windowId} window`,
    stats: [
      { label: 'Jobs', value: count(card.queue.affectedJobs) },
      { label: 'Nodes', value: count(card.queue.nodes.length) },
      { label: 'Hugging Face jobs', value: count(card.queue.huggingfaceAffectedJobs) },
      { label: 'Episodes', value: count(card.queue.episodes) },
    ],
    sources: source(ctx),
    tables: [
      {
        title: 'Physical nodes',
        meta: 'Open a node for its exact Buildkite logs',
        columns: nodeColumns(ctx, false),
        rows: card.bars.map((bar) => ({ card, bar })),
        pageSize: 25,
      },
    ],
    width: 'lg',
  }
}

/** Every affected physical node across the scope. */
export function nodesDetail(ctx: DnsContext): Detail {
  const rows = ctx.view.cards.flatMap((card) => card.bars.map((bar) => ({ card, bar })))
  return {
    id: 'dns-nodes',
    title: 'Physical nodes with DNS observations',
    subtitle: `${integer(rows.length)} queue and node pairs, ${ctx.view.windowId} window`,
    sources: source(ctx),
    tables: [{ columns: nodeColumns(ctx, true), rows, pageSize: 25 }],
    width: 'xl',
  }
}

/** Every retained exact log in the selected window and scope. */
export function evidenceDetail(ctx: DnsContext): Detail {
  const rows = ctx.view.cards.flatMap((card) =>
    card.bars.flatMap((bar) =>
      queueDnsEvidenceForNode(ctx.payload, ctx.view.windowId, card.queue.queue, bar.node.nodeRaw).filter((row) => Boolean(queueDnsEvidenceUrl(row))),
    ),
  )
  const jobs = ctx.view.totals.affectedJobs
  return {
    id: 'dns-evidence',
    title: 'Jobs with DNS observations',
    subtitle: `Retained exact Buildkite logs, ${ctx.view.windowId} window`,
    description: DESCRIPTION,
    notes:
      rows.length < jobs
        ? [{ tone: 'soft', text: `Exact links are retained for ${integer(rows.length)} of ${integer(jobs)} affected jobs; the counts above use the published aggregate.` }]
        : [],
    sources: source(ctx),
    tables: [
      {
        meta: `${integer(rows.length)} retained of ${integer(jobs)} jobs`,
        columns: evidenceColumns(true),
        rows,
        searchText: queueDnsEvidenceSearchText,
        emptyText: 'No exact log links were retained in this window.',
        pageSize: 30,
      },
    ],
    width: 'xl',
  }
}
