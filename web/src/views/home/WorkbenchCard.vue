<script setup lang="ts">
import { computed } from 'vue'
import { link, text, type ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ToggleGroup, ToggleGroupItem } from '@/components/ui/toggle-group'
import { useClock } from '@/composables/useClock'
import { useWorkItems } from '@/composables/useOperations'
import { useQueryState } from '@/composables/useQueryState'
import { age, observedCountLabel, shortDate, type Rec } from '@/domain/format'

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
const count = (k: Kind) =>
  observedCountLabel(work.value?.[k].length ?? 0, (k === 'prs' ? work.value?.prsSemantics : work.value?.issuesSemantics) ?? 'lower_bound')

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
  },
  {
    id: 'owner',
    label: kind.value === 'prs' ? 'Author' : 'Assignee',
    value: owner,
    cell: (row) => (owner(row) ? link(owner(row), github(owner(row))) : { text: 'Unassigned', muted: true }),
  },
  { id: 'status', label: 'Status', value: status, cell: (row) => text(status(row)) },
  {
    id: 'labels',
    label: 'Labels',
    value: (row) => labels(row).join(', '),
    cell: (row) => ({ text: labels(row).slice(0, 4).join(', ') || '—', muted: true }),
    sortable: false,
    hideable: true,
  },
  {
    id: 'updated',
    label: 'Updated',
    value: (row) => String(row.updated_at || ''),
    cell: (row) => text(row.updated_at ? age(row.updated_at, now.value) : null, row.updated_at ? shortDate(row.updated_at) : undefined),
  },
])
</script>

<template>
  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300" data-testid="workbench">
    <CardHeader>
      <CardTitle class="text-base">Engineering workbench</CardTitle>
      <CardDescription>Open ROCm CI issues and pull requests</CardDescription>
      <CardAction>
        <ToggleGroup v-model="kindModel" type="single" variant="outline" size="sm" aria-label="Work item kind">
          <ToggleGroupItem value="issues">Issues <span class="tabular-nums">{{ count('issues') }}</span></ToggleGroupItem>
          <ToggleGroupItem value="prs">PRs <span class="tabular-nums">{{ count('prs') }}</span></ToggleGroupItem>
        </ToggleGroup>
      </CardAction>
    </CardHeader>
    <CardContent>
      <DataTable
        :key="kind"
        :columns="columns"
        :rows="rows"
        :page-size="10"
        :label="kind === 'prs' ? 'Open pull requests' : 'Open issues'"
        search-placeholder="Filter title, person, label…"
        :empty-text="kind === 'prs' ? 'No open ROCm CI pull requests.' : 'No open ROCm CI issues.'"
      />
    </CardContent>
  </Card>
</template>
