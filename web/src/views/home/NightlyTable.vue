<script setup lang="ts">
import { computed } from 'vue'
import { link, rateCell, text, type CellSpec, type ColumnSpec } from '@/components/table/columns'
import DataTable from '@/components/table/DataTable.vue'
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { integer, shortDate, type Rec } from '@/domain/format'
import { nightlyCounts } from '@/domain/home'
import { nightlyCountLabel, nightlyFailureCount, nightlyState, type MovementKey } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'

const props = defineProps<{ builds: Rec[] }>()
const emit = defineEmits<{ open: [build: Rec, scope?: string] }>()

const LABELS: Record<MovementKey | 'hard' | 'soft', string> = {
  hard: 'Hard',
  soft: 'Soft',
  new: 'New',
  recurring: 'Recurring',
  fixed: 'Fixed',
}

/** A count that opens its job variants; zero is muted and unknown is a dash. */
function countCell(build: Rec, scope: keyof typeof LABELS, count: number | null): CellSpec {
  if (count === null) return text(null)
  if (!count) return { text: '0', muted: true }
  return { text: integer(count), onOpen: () => emit('open', build, scope) }
}
const severityCount = (build: Rec, scope: 'hard' | 'soft') => {
  const counts = nightlyCounts(build)
  return counts ? counts[scope] : null
}

const columns = computed<ColumnSpec<Rec>[]>(() => [
  {
    id: 'build',
    label: 'Build',
    value: (build) => Number(build.number || 0),
    cell: (build) => link(`#${build.number}`, exactPipelineBuildUrl(build, 'ci'), true),
    class: 'whitespace-nowrap',
  },
  {
    id: 'started',
    label: 'Started',
    value: (build) => String(build.created_at || ''),
    cell: (build) => text(shortDate(build.created_at)),
    class: 'hidden whitespace-nowrap sm:table-cell',
  },
  {
    id: 'passRate',
    label: 'Pass rate',
    numeric: true,
    value: (build) => nightlyCounts(build)?.passRate ?? -1,
    cell: (build) => {
      const counts = nightlyCounts(build)
      const state = nightlyState(build)
      if (!counts) return { text: state?.label ?? 'No test signal', tone: state?.tone ?? 'neutral' }
      return rateCell(counts.passRate * 100, [`${integer(counts.passed)} / ${integer(counts.total)}`, state?.label].filter(Boolean).join(' · '))
    },
    class: 'sm:whitespace-nowrap',
  },
  ...(['hard', 'soft'] as const).map(
    (scope): ColumnSpec<Rec> => ({
      id: scope,
      label: LABELS[scope],
      numeric: true,
      value: (build) => severityCount(build, scope) ?? -1,
      cell: (build) => countCell(build, scope, severityCount(build, scope)),
      class: scope === 'soft' ? 'hidden sm:table-cell' : undefined,
    }),
  ),
  ...(['new', 'recurring', 'fixed'] as const).map(
    (scope): ColumnSpec<Rec> => ({
      id: scope,
      label: LABELS[scope],
      numeric: true,
      hideable: true,
      class: 'hidden md:table-cell',
      value: (build) => nightlyFailureCount(build, scope) ?? -1,
      cell: (build) => countCell(build, scope, nightlyFailureCount(build, scope)),
    }),
  ),
])
</script>

<template>
  <Card class="gap-4 motion-safe:animate-in motion-safe:fade-in-0 motion-safe:slide-in-from-bottom-1 motion-safe:duration-300">
    <CardHeader>
      <CardTitle class="text-base">AMD nightlies</CardTitle>
      <CardDescription>{{ nightlyCountLabel(props.builds.length, 'builds') }} · open a row for its evidence, or a count for its job variants</CardDescription>
    </CardHeader>
    <CardContent class="px-4 sm:px-6" data-testid="nightly-table">
      <DataTable
        :columns="columns"
        :rows="props.builds"
        :page-size="10"
        :search-text="(build: Rec) => shortDate(build.created_at)"
        label="AMD nightlies"
        search-placeholder="Filter build or date…"
        empty-text="No AMD nightly build summary in this snapshot."
        :on-row-click="(build: Rec) => emit('open', build)"
      />
    </CardContent>
  </Card>
</template>
