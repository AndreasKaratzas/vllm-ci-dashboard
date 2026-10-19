<script setup lang="ts">
import { computed, ref } from 'vue'
import { Input } from '@/components/ui/input'
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from '@/components/ui/sheet'
import { Table, TableBody, TableCell, TableHead, TableHeader, TableRow } from '@/components/ui/table'
import { integer, shortDate, toneForState, value, type Rec, type Tone } from '@/domain/format'
import { nightlyBuildEvidence, nightlyFailureMovement } from '@/domain/nightly'
import { exactPipelineBuildUrl, exactPipelineEvidenceUrl } from '@/domain/urls'
import ToneBadge from './ToneBadge.vue'

const props = defineProps<{ build: Rec | null; title?: string; scope?: string }>()
const open = defineModel<boolean>('open', { required: true })
const filter = ref('')

const pipeline = computed(() => props.build?.source_pipeline || 'ci')
const evidence = computed(() => (props.build ? nightlyBuildEvidence(props.build, props.scope) : null))
const scoped = computed(() => evidence.value?.scope !== 'movement')
const severityScope = computed(() => evidence.value?.scope === 'hard' || evidence.value?.scope === 'soft')
const rows = computed(() => {
  const needle = filter.value.trim().toLowerCase()
  const all = evidence.value?.rows ?? []
  return needle
    ? all.filter((row) => [row.display_name, row.name, row.lifecycle, row.queue].join(' ').toLowerCase().includes(needle))
    : all
})
const buildUrl = computed(() => (props.build ? exactPipelineBuildUrl(props.build, pipeline.value) : ''))
const fields = computed(() => {
  const build = props.build ?? {}
  const movement = nightlyFailureMovement(build)
  const ev = evidence.value
  return [
    ['State', value(build.state)],
    ['Started', shortDate(build.created_at)],
    ['Job variants observed', integer(build.total_groups)],
    ['Test signal', build.has_test_results === false ? 'Unavailable' : 'Observed'],
    ['Dependency-blocked test jobs', build.test_jobs_blocked ? integer(build.test_jobs_blocked) : null],
    scoped.value
      ? [ev?.label ?? '', ev?.available ? integer(ev.rows.length) : 'Unavailable']
      : [
          'New failure / recurring failure / fixed',
          movement && movement.available !== false
            ? `${integer((movement.new || []).length)} / ${integer((movement.recurring || []).length)} / ${integer((movement.fixed || []).length)}`
            : 'Unavailable',
        ],
  ].filter((field): field is [string, string] => field[1] !== null)
})
const noSignalNote = computed(() => {
  const build = props.build
  if (!build || build.has_test_results !== false) return null
  const blocked = Number(build.test_jobs_blocked || 0)
  return {
    tone: (blocked ? 'hard' : 'soft') as Tone,
    text: blocked
      ? `This nightly failed before test execution. ${integer(blocked)} test jobs were dependency-blocked, so no pass/fail movement is inferred.`
      : 'This build has no parsed test-group signal. No pass/fail movement is inferred.',
  }
})

function changeTone(lifecycle: string): Tone {
  return lifecycle === 'New failure' ? 'hard' : lifecycle === 'Fixed' ? 'pass' : 'soft'
}
</script>

<template>
  <Sheet v-model:open="open">
    <SheetContent class="w-full overflow-y-auto sm:max-w-2xl">
      <SheetHeader>
        <SheetTitle>{{ title || (build?.number ? `AMD build #${build.number}` : 'AMD build') }}</SheetTitle>
        <SheetDescription>
          {{ scoped ? `${evidence?.label} evidence` : 'Build result and failure movement evidence' }}
        </SheetDescription>
      </SheetHeader>
      <div class="flex flex-col gap-4 px-4 pb-6">
        <dl class="grid grid-cols-2 gap-x-4 gap-y-2 text-sm">
          <template v-for="[label, val] in fields" :key="label">
            <dt class="text-muted-foreground">{{ label }}</dt>
            <dd class="font-medium">{{ val }}</dd>
          </template>
        </dl>
        <a v-if="buildUrl" :href="buildUrl" target="_blank" rel="noopener" class="text-sm font-medium text-primary hover:underline">
          Open Buildkite build ↗
        </a>
        <ToneBadge v-if="noSignalNote" :tone="noSignalNote.tone" class="whitespace-normal">{{ noSignalNote.text }}</ToneBadge>
        <p v-if="evidence && !evidence.available" class="text-sm text-warning">
          {{
            scoped
              ? `${evidence.label} evidence is unavailable for this build.`
              : 'Failure movement is unavailable for this build. Raw build outcomes remain visible below.'
          }}
        </p>
        <template v-if="evidence?.rows.length">
          <Input v-model="filter" placeholder="Filter job variant, change, or queue" />
          <Table>
            <TableHeader>
              <TableRow>
                <TableHead>Job variant</TableHead>
                <TableHead>{{ severityScope ? 'Current result' : 'Change' }}</TableHead>
                <TableHead>Queue</TableHead>
              </TableRow>
            </TableHeader>
            <TableBody>
              <TableRow v-for="(row, index) in rows" :key="index">
                <TableCell class="max-w-80 whitespace-normal">
                  <a
                    v-if="exactPipelineEvidenceUrl(row, pipeline)"
                    :href="exactPipelineEvidenceUrl(row, pipeline)"
                    target="_blank"
                    rel="noopener"
                    class="hover:underline"
                    >{{ row.display_name || row.name }}</a
                  >
                  <span v-else>{{ row.display_name || row.name }}</span>
                </TableCell>
                <TableCell>
                  <ToneBadge v-if="severityScope" :tone="toneForState(row.current_state || row.state || row.result)">{{ value(row.current_state || row.state || row.result) }}</ToneBadge>
                  <ToneBadge v-else :tone="changeTone(row.lifecycle)">{{ row.lifecycle }}</ToneBadge>
                </TableCell>
                <TableCell class="font-mono text-xs">{{ value(row.queue) }}</TableCell>
              </TableRow>
            </TableBody>
          </Table>
        </template>
        <p v-else-if="evidence?.available && scoped" class="text-sm text-muted-foreground">
          No {{ evidence.label.toLowerCase() }} are observed in this build.
        </p>
      </div>
    </SheetContent>
  </Sheet>
</template>
