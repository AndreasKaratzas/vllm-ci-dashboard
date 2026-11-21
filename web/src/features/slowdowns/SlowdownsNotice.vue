<script setup lang="ts">
import { ChevronRight } from '@lucide/vue'
import { computed } from 'vue'
import { RouterLink } from 'vue-router'
import Callout from '@/components/feedback/Callout.vue'
import { Button } from '@/components/ui/button'
import { useOperations } from '@/composables/useOperations'
import { counted } from '@/domain/format'
import { mainCiRecord, mainCiSummary } from '@/domain/mainCi'

// The count comes from the manifest shell, so pages show it without downloading the main CI section.
const { ops } = useOperations()
const count = computed(() => mainCiSummary(mainCiRecord(ops.value))?.activeSlowdowns ?? 0)
</script>

<template>
  <Callout v-if="count" tone="soft" :title="`${counted(count, 'AMD test step')} on main ${count === 1 ? 'is' : 'are'} running slower than before`" data-testid="slowdowns-notice">
    <slot>Slowdowns lists each one with the build it started in and the vLLM commits around it.</slot>
    <template #action>
      <Button variant="outline" size="sm" as-child>
        <RouterLink :to="{ name: 'analytics', query: { view: 'slowdowns' } }">Open slowdowns <ChevronRight /></RouterLink>
      </Button>
    </template>
  </Callout>
</template>
