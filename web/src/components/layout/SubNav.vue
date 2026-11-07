<script setup lang="ts">
import { computed } from 'vue'
import { RouterLink, useRoute, type LocationQuery } from 'vue-router'
import { cn } from '@/lib/utils'

export interface SubNavItem {
  value: string
  label: string
  count?: number
}

const props = withDefaults(defineProps<{ items: SubNavItem[]; defaultValue: string; param?: string; label?: string }>(), {
  param: 'view',
  label: 'Sections',
})

const route = useRoute()
const current = computed(() => {
  const value = route.query[props.param]
  return typeof value === 'string' && props.items.some((item) => item.value === value) ? value : props.defaultValue
})

// Switching section keeps unrelated query state and leaves the default out of the URL.
function target(value: string) {
  const query: LocationQuery = { ...route.query }
  if (value === props.defaultValue) delete query[props.param]
  else query[props.param] = value
  return { path: route.path, query }
}
</script>

<template>
  <nav
    :aria-label="label"
    class="-mx-4 overflow-x-auto px-4 [scrollbar-width:none] [mask-image:linear-gradient(to_right,transparent,black_1rem,black_calc(100%-1rem),transparent)] md:mx-0 md:px-0 md:[mask-image:none]"
    data-slot="sub-nav"
  >
    <ul class="flex w-max min-w-full gap-5 border-b">
      <li v-for="item in items" :key="item.value">
        <RouterLink
          :to="target(item.value)"
          :aria-current="item.value === current ? 'page' : undefined"
          :class="cn(
            '-mb-px inline-flex items-center gap-1.5 rounded-t-sm border-b-2 border-transparent px-1 pt-1 pb-2.5 text-sm font-medium whitespace-nowrap text-muted-foreground transition-colors outline-none hover:border-border hover:text-foreground focus-visible:ring-3 focus-visible:ring-ring/50',
            item.value === current && 'border-brand text-foreground hover:border-brand',
          )"
        >
          {{ item.label }}
          <span v-if="item.count !== undefined" class="rounded-full bg-muted px-1.5 text-xs tabular-nums text-muted-foreground">{{ item.count }}</span>
        </RouterLink>
      </li>
    </ul>
  </nav>
</template>
