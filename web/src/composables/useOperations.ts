import { computed, type Ref } from 'vue'
import { populationSemantics, type Rec } from '@/domain/format'
import { useDataset, type Dataset } from './useDataset'

export interface WorkItems {
  prs: Rec[]
  issues: Rec[]
  prsSemantics: string
  issuesSemantics: string
}

// Views replace their content with the error panel, so a failed background refresh
// that still has data to show is not reported as an error here.
function blockingError(dataset: Dataset): Ref<string | null> {
  return computed(() => (dataset.data.value === null ? dataset.error.value : null))
}

/** The manifest shell: header, command palette and Home. */
export function useOperations() {
  const dataset = useDataset({ sections: [] })
  return { ops: dataset.data, error: blockingError(dataset), loading: dataset.loading, retry: dataset.refresh }
}

/** Shell merged with the named manifest sections. */
export function useOperationSections(names: string[]) {
  const dataset = useDataset({ sections: names })
  return { data: dataset.data, error: blockingError(dataset), loading: dataset.loading, retry: dataset.refresh }
}

const NO_WORK_ITEMS: WorkItems = { prs: [], issues: [], prsSemantics: 'lower_bound', issuesSemantics: 'lower_bound' }

export function useWorkItems(): Ref<WorkItems | null> {
  const prs = useDataset('prs')
  const issues = useDataset('issues')
  return computed(() => {
    const prsData = prs.data.value
    const issuesData = issues.data.value
    // Project activity is secondary: a missing source shows as empty, never as a page error.
    if ((prs.error.value && !prsData) || (issues.error.value && !issuesData)) return NO_WORK_ITEMS
    if (!prsData || !issuesData) return null
    return {
      prs: prsData.prs || [],
      issues: issuesData.issues || [],
      prsSemantics: populationSemantics(prsData),
      issuesSemantics: populationSemantics(issuesData),
    }
  })
}
