export interface TooltipRow {
  label: string
  value: string | number
  color?: string
}

export interface TooltipSpec {
  title: string
  rows?: TooltipRow[]
  hint?: string
}

const ESCAPES: Record<string, string> = { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }

export function escapeHtml(text: unknown): string {
  return String(text ?? '').replace(/[&<>"']/g, (c) => ESCAPES[c]!)
}

/** Unovis tooltips take an HTML string; every caller goes through here so data is always escaped. */
export function tooltipHtml({ title, rows = [], hint }: TooltipSpec): string {
  const body = rows
    .map(
      (row) =>
        '<div class="flex items-center gap-2">' +
        (row.color ? `<span class="size-2.5 shrink-0 rounded-[2px]" style="background:${escapeHtml(row.color)}"></span>` : '') +
        `<span class="flex-1 text-muted-foreground">${escapeHtml(row.label)}</span>` +
        `<span class="font-mono font-medium tabular-nums text-foreground">${escapeHtml(typeof row.value === 'number' ? row.value.toLocaleString('en-US') : row.value)}</span>` +
        '</div>',
    )
    .join('')
  return (
    '<div class="grid min-w-[9rem] max-w-[18rem] gap-1.5 rounded-lg border border-border/50 bg-background px-2.5 py-1.5 text-xs shadow-xl">' +
    `<div class="font-medium text-foreground">${escapeHtml(title)}</div>` +
    (body ? `<div class="grid gap-1">${body}</div>` : '') +
    (hint ? `<div class="text-muted-foreground">${escapeHtml(hint)}</div>` : '') +
    '</div>'
  )
}
