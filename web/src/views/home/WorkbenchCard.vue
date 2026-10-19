<script setup lang="ts">
import { computed } from 'vue'
import { shortDay } from '@/components/charts/chart'
import { link, text, type ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Badge } from '@/components/ui/badge'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useClock } from '@/composables/useClock'
import { useWorkItems } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { age, integer, shortDate, type Rec } from '@/domain/format'

const KINDS = ['issues', 'prs'] as const
type Kind = (typeof KINDS)[number]

const work = useWorkItems()
const now = useClock()
const kind = useQueryState<Kind>('work', { default: 'issues', allowed: KINDS })
const kindModel = computed({
  get: () => kind.value,
  set: (value: Kind | '') => {
    if (value) kind.value = value
  },
})

const rows = computed<Rec[]>(() => (kind.value === 'prs' ? work.value?.prs : work.value?.issues) ?? [])
const KIND_LABEL: Record<Kind, string> = { issues: 'Issues', prs: 'PRs' }
// A lower-bound population ("the board may list more") reads as "3+"; an empty one is just 0.
const count = (k: Kind) => {
  const n = work.value?.[k].length ?? 0
  const lowerBound = ((k === 'prs' ? work.value?.prsSemantics : work.value?.issuesSemantics) ?? 'lower_bound') === 'lower_bound'
  return { text: `${integer(n)}${lowerBound && n ? '+' : ''}`, title: lowerBound && n ? `At least ${integer(n)}` : `${integer(n)}` }
}
const updatedDay = (ts: unknown) => {
  const ms = Date.parse(String(ts || ''))
  return Number.isFinite(ms) ? shortDay(ms) : shortDate(ts)
}

const github = (login: string) => `https://github.com/${encodeURIComponent(login)}`
const owner = (row: Rec): string => (kind.value === 'prs' ? row.author : (row.assignees || [])[0]) || ''
const status = (row: Rec): string => {
  if (kind.value === 'prs') return row.merged ? 'Merged' : row.draft ? 'Draft' : 'Open'
  return String(row.project_status || row.state || '')
}
const labels = (row: Rec): string[] => row.custom_tags || row.labels || []

const columns = computed<ColumnSpec<Rec>[]>(() => [
  {
    id: 'title',
    label: kind.value === 'prs' ? 'Pull request' : 'Issue',
    value: (row) => `#${row.number} ${row.title}`,
    cell: (row) => link(`#${row.number} ${row.title}`, row.html_url),
    class: 'min-w-48 [overflow-wrap:anywhere]',
  },
  {
    id: 'owner',
    label: kind.value === 'prs' ? 'Author' : 'Assignee',
    value: owner,
    cell: (row) => (owner(row) ? link(owner(row), github(owner(row))) : { text: 'Unassigned', muted: true }),
    class: 'hidden whitespace-nowrap sm:table-cell',
  },
  { id: 'status', label: 'Status', value: status, cell: (row) => text(status(row)), class: 'hidden whitespace-nowrap md:table-cell' },
  {
    id: 'labels',
    label: 'Labels',
    value: (row) => labels(row).join(', '),
    cell: (row) => ({ text: labels(row).join(', '), tags: labels(row).slice(0, 4) }),
    sortable: false,
    hideable: true,
    class: 'hidden xl:table-cell',
  },
  {
    id: 'updated',
    label: 'Updated',
    value: (row) => String(row.updated_at || ''),
    cell: (row) => text(row.updated_at ? age(row.updated_at, now.value) : null, row.updated_at ? updatedDay(row.updated_at) : undefined),
    class: 'whitespace-nowrap',
  },
])
</script>

<template>
  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="workbench">
    <CardHeader>
      <CardTitle class="text-base">Engineering workbench</CardTitle>
      <CardDescription>Open ROCm CI issues and pull requests from the project board</CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6">
      <DataTable
        :key="kind"
        :columns="columns"
        :rows="rows"
        :page-size="10"
        :label="kind === 'prs' ? 'Open pull requests' : 'Open issues'"
        search-placeholder="Filter title, person, label…"
        :empty-text="kind === 'prs' ? 'No open ROCm CI pull requests.' : 'No open ROCm CI issues.'"
      >
        <template #toolbar>
          <ToggleGroup v-model="kindModel" type="single" variant="outline" size="sm" aria-label="Work item kind">
            <ToggleGroupItem v-for="k in KINDS" :key="k" :value="k" class="gap-1.5 px-3">
              {{ KIND_LABEL[k] }}
              <Badge variant="secondary" class="h-5 min-w-5 rounded-full px-1.5 tabular-nums" :title="count(k).title">{{ count(k).text }}</Badge>
            </ToggleGroupItem>
          </ToggleGroup>
        </template>
      </DataTable>
    </CardContent>
  </Card>
</template>
