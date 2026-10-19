<script setup lang="ts">
import { CircleDot, GitPullRequest, Hammer, LayoutDashboard } from '@lucide/vue'
import { useMagicKeys, whenever } from '@vueuse/core'
import { computed } from 'vue'
import { useRouter } from 'vue-router'
import {
  CommandDialog,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
  CommandSeparator,
} from '@/components/ui/command'
import { useOperations, useWorkItems } from '@/composables/useOperations'
import { shortDate, type Rec } from '@/domain/format'
import { latestAmd } from '@/domain/nightly'
import { exactPipelineBuildUrl } from '@/domain/urls'
import { NAV_TABS } from '@/router'

const open = defineModel<boolean>('open', { required: true })
const router = useRouter()
const { ops } = useOperations()
const work = useWorkItems()

const keys = useMagicKeys({
  passive: false,
  onEventFired(e) {
    if (e.key === 'k' && (e.metaKey || e.ctrlKey) && e.type === 'keydown') e.preventDefault()
  },
})
whenever(() => (keys['Meta+K']?.value || keys['Ctrl+K']?.value) ?? false, () => (open.value = !open.value))

const nightlies = computed<Rec[]>(() => (latestAmd(ops.value).builds || []).filter((b: Rec) => exactPipelineBuildUrl(b, 'ci')))

function go(path: string) {
  open.value = false
  void router.push(path)
}
function visit(url: string) {
  open.value = false
  window.open(url, '_blank', 'noopener')
}
</script>

<template>
  <CommandDialog v-model:open="open" title="Search the dashboard" description="Jump to a view, nightly, issue, or pull request">
    <CommandInput placeholder="Type a view, build number, issue, or PR…" />
    <CommandList>
      <CommandEmpty>No results found.</CommandEmpty>
      <CommandGroup heading="Views">
        <CommandItem v-for="tab in NAV_TABS" :key="tab.name" :value="`view ${tab.label}`" @select="go(tab.path)">
          <LayoutDashboard /> {{ tab.label }}
        </CommandItem>
      </CommandGroup>
      <CommandSeparator />
      <CommandGroup heading="AMD nightlies">
        <CommandItem
          v-for="build in nightlies"
          :key="build.number"
          :value="`nightly #${build.number} ${build.state}`"
          @select="visit(exactPipelineBuildUrl(build, 'ci'))"
        >
          <Hammer /> #{{ build.number }}
          <span class="ml-auto text-xs text-muted-foreground">{{ build.state }} · {{ shortDate(build.created_at) }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandSeparator />
      <CommandGroup heading="Issues">
        <CommandItem
          v-for="issue in work?.issues ?? []"
          :key="issue.number"
          :value="`issue #${issue.number} ${issue.title}`"
          @select="visit(issue.html_url)"
        >
          <CircleDot /> <span class="truncate">#{{ issue.number }} {{ issue.title }}</span>
        </CommandItem>
      </CommandGroup>
      <CommandGroup heading="Pull requests">
        <CommandItem v-for="pr in work?.prs ?? []" :key="pr.number" :value="`pr #${pr.number} ${pr.title}`" @select="visit(pr.html_url)">
          <GitPullRequest /> <span class="truncate">#{{ pr.number }} {{ pr.title }}</span>
        </CommandItem>
      </CommandGroup>
    </CommandList>
  </CommandDialog>
</template>
