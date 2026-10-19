<script setup lang="ts">
import type { TabsListProps } from "reka-ui"
import type { HTMLAttributes } from "vue"
import { reactiveOmit } from "@vueuse/core"
import { cn } from "@/lib/utils"
import { TabsList } from "reka-ui"
import { tabsListVariants, type TabsListVariants } from "."

const props = defineProps<TabsListProps & { class?: HTMLAttributes["class"], variant?: TabsListVariants["variant"] }>()

const delegatedProps = reactiveOmit(props, "class", "variant")
</script>

<template>
  <TabsList
    data-slot="tabs-list"
    :data-variant="variant ?? 'default'"
    v-bind="delegatedProps"
    :class="cn(tabsListVariants({ variant }), props.class)"
  >
    <slot />
  </TabsList>
</template>
