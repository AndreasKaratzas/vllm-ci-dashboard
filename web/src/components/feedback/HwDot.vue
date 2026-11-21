<script setup lang="ts">
import { computed } from 'vue'
import { Badge } from '@/components/ui/badge'
import { hardwareColor, hardwareFamily } from '@/lib/palette'

const props = defineProps<{ family: string; label?: string }>()

const text = computed(() => props.label || hardwareFamily(props.family) || props.family)
</script>

<template>
  <Badge variant="outline" class="gap-1.5 font-medium text-foreground">
    <span class="size-1.5 shrink-0 rounded-full" :style="{ backgroundColor: hardwareColor(family) }" aria-hidden="true" />
    {{ text }}
  </Badge>
</template>
