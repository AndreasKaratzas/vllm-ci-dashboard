<script setup lang="ts">
import { Activity, CircleDot, Clock, CornerDownRight, FolderGit2, GitPullRequest, LayoutDashboard, Moon, Sun } from '@lucide/vue'
import { useMagicKeys, whenever } from '@vueuse/core'
import { computed } from 'vue'
import { useRouter, type RouteLocationRaw } from 'vue-router'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command'
import { useOperations, useWorkItems } from '@/composables/useOperations'
import { useTheme } from '@/composables/useTheme'
import { shortDate, type Rec } from '@/domain/format'
import { nightlyResultTone } from '@/domain/home'
import { latestAmd } from '@/domain/nightly'
import { useFreshness } from '@/features/freshness/useFreshness'
import { toneClasses, TONE_LABEL } from '@/lib/palette'
import { NAV_TABS } from '@/router'
import { SUB_VIEWS, subViewQuery } from './navigation'

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
    (SUB_VIEWS[tab.name]?.items ?? []).map((view) => ({
      key: `${tab.name}-${view.value}`,
      tab: tab.label,
      label: view.label,
      to: { path: tab.path, query: subViewQuery(tab.name, view.value) },
    })),
  ),
)
const nightlies = computed<Rec[]>(() => (latestAmd(ops.value).builds || []).filter((b: Rec) => b.number))

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
    description="Jump to a view, nightly, issue, or pull request, or run an action"
  >
    <CommandInput placeholder="Search views, nightlies, actions…" class="pr-8" />
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
