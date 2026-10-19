import type { VariantProps } from "class-variance-authority"
import { cva } from "class-variance-authority"

export { default as Tabs } from "./Tabs.vue"
export { default as TabsContent } from "./TabsContent.vue"
export { default as TabsList } from "./TabsList.vue"
export { default as TabsTrigger } from "./TabsTrigger.vue"

// 'default' is the segmented control for in-card toggles; 'line' is the
// underlined page-level navigation.
export const tabsListVariants = cva("group/tabs-list inline-flex w-fit items-center text-muted-foreground", {
  variants: {
    variant: {
      default: "bg-muted h-9 justify-center rounded-lg p-0.75",
      line: "h-auto justify-start gap-5 border-b",
    },
  },
  defaultVariants: {
    variant: "default",
  },
})
export type TabsListVariants = VariantProps<typeof tabsListVariants>
