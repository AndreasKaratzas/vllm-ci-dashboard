<script setup lang="ts">
import { computed } from 'vue'
import ProportionBar, { type ProportionSegment } from '@/components/charts/ProportionBar.vue'
import InfoPopover from '@/components/feedback/InfoPopover.vue'
import { Card, CardAction, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card'
import { ATTRIBUTION_REASON, type AgentAttribution, type AttributionBreakdown } from '@/domain/agentHealth'
import { integer } from '@/domain/format'
import { SERIES, tint, type PaletteColor } from '@/lib/palette'

// What caused the window's failures: the node itself, or something every node would have hit.
const props = defineProps<{ breakdown: AttributionBreakdown; windowId: string; lowerBound: boolean }>()
const emit = defineEmits<{ open: [AgentAttribution] }>()

// Node-side causes share the primary hue; causes elsewhere share the secondary one.
const COLOR: Record<AgentAttribution, PaletteColor> = {
  node: SERIES.primary,
  unknown: tint(SERIES.primary, 45),
  build: SERIES.secondary,
  test: tint(SERIES.secondary, 50),
  infra: SERIES.muted,
}
const count = (n: number) => (props.lowerBound && n > 0 ? '≥' : '') + integer(n)
const segments = computed<ProportionSegment[]>(() =>
  props.breakdown.shares.map((share) => ({ key: share.attribution, label: share.label, count: share.count, color: COLOR[share.attribution] })),
)
const summary = computed(() => {
  const { total, notNode } = props.breakdown
  if (!total) return `No failures in ${props.windowId}.`
  const node = props.breakdown.shares.find((share) => share.attribution === 'node')!.count
  return `${count(notNode)} of ${count(total)} failures in ${props.windowId} were build-wide, test or shared-infrastructure failures, not node faults; ${count(node)} ${node === 1 ? 'is' : 'are'} attributed to a node.`
})
const RULES: [string, string[]][] = [
  ['Node', ['agent_lost', 'node_repeats']],
  ['Unexplained', ['isolated', 'job_never_started']],
  ['Build-wide', ['upstream_build_wide', 'build_wide']],
  ['Test or code', ['failed_on_retry_elsewhere', 'upstream_mirror_failed', 'group_fails_elsewhere', 'group_not_passing_elsewhere']],
  ['Shared infrastructure', ['agent_loss_burst', 'start_failure_burst', 'build_wide_transient']],
]
</script>

<template>
  <Card class="gap-4" data-testid="agent-attribution">
    <CardHeader>
      <CardTitle>What caused the failures</CardTitle>
      <CardDescription data-testid="agent-attribution-summary">{{ summary }}</CardDescription>
      <CardAction>
        <InfoPopover label="About failure attribution" title="How a failure is attributed">
          <dl class="flex flex-col gap-2">
            <div v-for="[label, reasons] in RULES" :key="label">
              <dt class="font-medium text-foreground">{{ label }}</dt>
              <dd v-for="reason in reasons" :key="reason">{{ ATTRIBUTION_REASON[reason] }}</dd>
            </div>
          </dl>
        </InfoPopover>
      </CardAction>
    </CardHeader>
    <CardContent>
      <ProportionBar
        :segments="segments"
        :label="`Failures in ${windowId} by attributed cause`"
        :value-format="count"
        :on-select="(segment: ProportionSegment) => emit('open', segment.key as AgentAttribution)"
      />
    </CardContent>
  </Card>
</template>
