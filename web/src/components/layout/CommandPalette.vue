<script setup lang="ts">
import { Activity, Boxes, CircleDot, Clock, CornerDownRight, FolderGit2, Gauge, GitPullRequest, LayoutDashboard, Moon, Siren, Sun } from '@lucide/vue'
import { useMagicKeys, whenever } from '@vueuse/core'
import { computed, effectScope, onScopeDispose, shallowRef, watch, type ShallowRef } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useDataset } from '@/composables/useDataset'
import { useOperationSections, useOperations, useWorkItems } from '@/composables/useOperations'
import { useTheme } from '@/composables/useTheme'
import { amdLogicalInventory, amdLogicalStateLabel, amdLogicalStateTone, sortLogicalRows } from '@/domain/ciHealth'
import { shortDate, type Rec } from '@/domain/format'
import { nightlyResultTone } from '@/domain/home'
import { buildOutcome, buildTone, groupLabel, mainBuilds, mainIncidents } from '@/domain/mainCi'
import { latestAmd } from '@/domain/nightly'
import { perfModels, perfQuery, perfState } from '@/domain/perf'
import { logicalGroupDetail } from '@/features/amd-groups/details'
import { useFreshness } from '@/features/freshness/useFreshness'
import { toneClasses, TONE_LABEL } from '@/lib/palette'
import { NAV_TABS } from '@/router'
import { pageViews, subViewQuery } from './navigation'

const open = defineModel<boolean>('open', { required: true })
const router = useRouter()
const { ops } = useOperations()
const work = useWorkItems()
const { dark, toggle } = useTheme()
const freshness = useFreshness()

const keys = useMagicKeys({
  passive: false,
  onEventFired(e) {
    if (e.key === 'k' && (e.metaKey || e.ctrlKey) && e.type === 'keydown') e.preventDefault()
  },
})
whenever(() => (keys['Meta+K']?.value || keys['Ctrl+K']?.value) ?? false, () => (open.value = !open.value))

const subViews = computed(() =>
  NAV_TABS.flatMap((tab) =>
    (pageViews(tab.name)?.items ?? []).map((view) => ({
      key: `${tab.name}-${view.value}`,
      tab: tab.label,
      label: view.label,
      to: { path: tab.path, query: subViewQuery(tab.name, view.value) },
    })),
  ),
)
const nightlies = computed<Rec[]>(() => (latestAmd(ops.value).builds || []).filter((b: Rec) => b.number))

// Main CI, test groups and perf models come from their pages' datasets, loaded the first time the palette opens.
const mainData: ShallowRef<Rec | null> = shallowRef(null)
const groupsData: ShallowRef<Rec | null> = shallowRef(null)
const perfData: ShallowRef<Rec | null> = shallowRef(null)
const scope = effectScope()
onScopeDispose(() => scope.stop())
const stopLoading = watch(open, (isOpen) => {
  if (!isOpen) return
  scope.run(() => {
    const main = useOperationSections(['main_ci'])
    const groups = useOperationSections(['amd_test_health'])
    watch(main.data, (data) => (mainData.value = data), { immediate: true })
    const perf = useDataset('perf_eval')
    watch(groups.data, (data) => (groupsData.value = data), { immediate: true })
    watch(perf.data, (data) => (perfData.value = data), { immediate: true })
  })
  stopLoading()
})
// The newest main builds; older ones stay one click away on the Main CI page.
const MAIN_BUILD_LIMIT = 50
const mainBuildRows = computed(() => mainBuilds(mainData.value?.main_ci).slice(0, MAIN_BUILD_LIMIT))
const mainIncidentRows = computed(() => mainIncidents(mainData.value?.main_ci).active)
const testGroups = computed<Rec[]>(() => sortLogicalRows(amdLogicalInventory(groupsData.value?.amd_test_health).rows || []))
const models = computed<string[]>(() => [...new Set(perfModels(perfData.value).map((model) => String(model.model || '')).filter(Boolean))].sort())
function openGroup(row: Rec) {
  open.value = false
  const amdHealth = groupsData.value?.amd_test_health
  freshness.sheets.open(logicalGroupDetail(row, { amdHealth, latestBuild: (amdHealth?.summary || {}).latest_build_number }))
}

const REPO = 'https://github.com/AndreasKaratzas/vllm-ci-dashboard'
const BUILDKITE = 'https://buildkite.com/vllm/amd-ci'

function go(to: RouteLocationRaw) {
  open.value = false
  void router.push(to)
}
function visit(url: string) {
  open.value = false
  window.open(url, '_blank', 'noopener')
}
function run(action: () => void) {
  open.value = false
  action()
}
</script>

<template>
  <CommandDialog
    v-model:open="open"
    class="top-[12vh] translate-y-0 sm:top-[15vh]"
    title="Search the dashboard"
    description="Jump to a view, main build or incident, nightly, test group, perf model, issue, or pull request, or run an action"
  >
    <CommandInput placeholder="Search views, main builds, incidents, test groups…" class="pr-8" />
    <CommandList class="max-h-[min(28rem,70vh)]" aria-label="Search results">
      <CommandEmpty>No results found.</CommandEmpty>
      <CommandGroup heading="Views">
        <CommandItem v-for="tab in NAV_TABS" :key="tab.name" :value="`view ${tab.label}`" @select="go(tab.path)">
          <LayoutDashboard /> {{ tab.label }}
        </CommandItem>
      </CommandGroup>
      <CommandGroup heading="Sections">
        <CommandItem v-for="view in subViews" :key="view.key" :value="`section ${view.tab} ${view.label}`" @select="go(view.to)">
          <CornerDownRight />
          <span class="text-muted-foreground">{{ view.tab }}</span>
          <span>{{ view.label }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup v-if="mainBuildRows.length" heading="Main builds">
        <CommandItem
          v-for="build in mainBuildRows"
          :key="build.number"
          :value="`main build #${build.number} ${buildOutcome(build)} ${build.headline}`"
          @select="go({ name: 'main', query: { build: String(build.number) } })"
        >
          <span class="mx-1 size-2 shrink-0 rounded-full" :class="toneClasses(buildTone(build), 'fill')" aria-hidden="true" />
          <span class="font-mono">#{{ build.number }}</span>
          <span class="truncate text-muted-foreground">{{ build.headline }}</span>
          <span class="ml-auto shrink-0 text-xs text-muted-foreground">{{ buildOutcome(build) }} · {{ shortDate(build.createdAt) }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup v-if="mainIncidentRows.length" heading="Main incidents">
        <CommandItem
          v-for="incident in mainIncidentRows"
          :key="incident.groupId"
          :value="`main incident ${incident.name} ${incident.hardware} ${incident.queue} ${incident.severity}`"
          @select="go({ name: 'main', query: { view: 'incidents', incident: incident.groupId } })"
        >
          <Siren />
          <span class="truncate">{{ groupLabel(incident.name) }}</span>
          <span class="ml-auto inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
            <span class="size-2 rounded-full" :class="toneClasses(incident.severity, 'fill')" aria-hidden="true" />
            {{ TONE_LABEL[incident.severity] }}
          </span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup heading="AMD nightlies">
        <CommandItem
          v-for="build in nightlies"
          :key="build.number"
          :value="`nightly #${build.number} ${build.state}`"
          @select="go({ name: 'home', query: { build: String(build.number) } })"
        >
          <span class="mx-1 size-2 shrink-0 rounded-full" :class="toneClasses(nightlyResultTone(build), 'fill')" aria-hidden="true" />
          <span class="font-mono">#{{ build.number }}</span>
          <span class="sr-only">{{ TONE_LABEL[nightlyResultTone(build)] }}</span>
          <span class="ml-auto text-xs text-muted-foreground">{{ build.state }} · {{ shortDate(build.created_at) }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup v-if="testGroups.length" heading="AMD test groups">
        <CommandItem
          v-for="group in testGroups"
          :key="group.logical_key || group.label"
          :value="`test group ${group.label} ${group.logical_key} ${(group.hardware_states || []).map((s: Rec) => s.hardware).join(' ')}`"
          @select="openGroup(group)"
        >
          <Boxes />
          <span class="truncate">{{ group.label || group.logical_key }}</span>
          <span class="ml-auto inline-flex shrink-0 items-center gap-1.5 text-xs text-muted-foreground">
            <span class="size-2 rounded-full" :class="toneClasses(amdLogicalStateTone(group.state), 'fill')" aria-hidden="true" />
            {{ amdLogicalStateLabel(group.state) }}
          </span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup v-if="models.length" heading="Perf models">
        <CommandItem v-for="model in models" :key="model" :value="`perf model ${model}`" @select="go({ path: '/perf', query: perfQuery(perfState({ model })) })">
          <Gauge /> <span class="truncate">{{ model }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup heading="Actions">
        <CommandItem value="action toggle theme dark light" @select="run(toggle)">
          <Sun v-if="dark" /><Moon v-else /> Switch to {{ dark ? 'light' : 'dark' }} theme
        </CommandItem>
        <CommandItem value="action data freshness sources" @select="run(freshness.open)"><Clock /> Show data freshness</CommandItem>
        <CommandItem value="action open buildkite amd ci" @select="visit(BUILDKITE)"><Activity /> Open Buildkite AMD CI</CommandItem>
        <CommandItem value="action open dashboard repository github" @select="visit(REPO)"><FolderGit2 /> Open dashboard repository</CommandItem>
      </CommandGroup>
      <CommandGroup v-if="work?.issues.length" heading="Issues">
        <CommandItem v-for="issue in work.issues" :key="issue.number" :value="`issue #${issue.number} ${issue.title}`" @select="visit(issue.html_url)">
          <CircleDot /> <span class="truncate">#{{ issue.number }} {{ issue.title }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup v-if="work?.prs.length" heading="Pull requests">
        <CommandItem v-for="pr in work.prs" :key="pr.number" :value="`pr #${pr.number} ${pr.title}`" @select="visit(pr.html_url)">
          <GitPullRequest /> <span class="truncate">#{{ pr.number }} {{ pr.title }}</span>
        </CommandItem>
      </CommandGroup>
    </CommandList>
  </CommandDialog>
</template>
