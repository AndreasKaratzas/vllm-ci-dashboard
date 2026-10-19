import { shallowRef, ref } from 'vue'
import { clearDataCache, fetchJSON } from '@/data/fetch'
import { loadOperations } from '@/data/operations'
import { populationSemantics, type Rec } from '@/domain/format'

// One shared load per page view: the header, command palette and Home all read it.
const ops = shallowRef<Rec | null>(null)
const opsError = ref<string | null>(null)
const opsLoading = ref(false)
let opsRequest: Promise<void> | null = null

export interface WorkItems {
  prs: Rec[]
  issues: Rec[]
  prsSemantics: string
  issuesSemantics: string
}
const work = shallowRef<WorkItems | null>(null)
let workRequest: Promise<void> | null = null

function loadOps(force = false) {
  if (opsRequest && !force) return opsRequest
  if (force) clearDataCache()
  opsLoading.value = true
  opsError.value = null
  opsRequest = loadOperations([])
    .then((data) => {
      ops.value = data
    })
    .catch((e) => {
      opsError.value = e instanceof Error ? e.message : String(e)
      opsRequest = null
    })
    .finally(() => {
      opsLoading.value = false
    })
  return opsRequest
}

function loadWork() {
  workRequest ??= Promise.all([fetchJSON<Rec>('data/vllm/prs.json'), fetchJSON<Rec>('data/vllm/issues.json')])
    .then(([prs, issues]) => {
      work.value = {
        prs: prs.prs || [],
        issues: issues.issues || [],
        prsSemantics: populationSemantics(prs),
        issuesSemantics: populationSemantics(issues),
      }
    })
    .catch(() => {
      // Project activity is secondary; operations views still render.
      work.value = { prs: [], issues: [], prsSemantics: 'lower_bound', issuesSemantics: 'lower_bound' }
    })
  return workRequest
}

export function useOperations() {
  void loadOps()
  return { ops, error: opsError, loading: opsLoading, retry: () => loadOps(true) }
}

export function useWorkItems() {
  void loadWork()
  return work
}
