<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { DATASETS } from '@/data/sources'
import { shortDate, value, type Rec } from '@/domain/format'

defineProps<{ payload: Rec }>()
const [LIVE, PAGES] = DATASETS.docker_builds.paths
</script>

<template>
  <p>
    Every Buildkite job of the ROCm docker build steps on the amd-cpu queue of vllm/ci and vllm/amd-ci, for main, pull-request and other branch builds.
    Run time is from the job's start to its finish; wait is from when it became runnable to its start.
  </p>
  <p>
    Statistics use finished final attempts only. Retried attempts and jobs still running, canceled before they started, or never run are listed but not
    timed. Percentiles are nearest-rank over the selected window.
  </p>
  <p>
    The base step reuses its cached image when it finishes in under 10 minutes. From 10 minutes to 2 hours it rebuilt the runtime layer (a partial
    rebuild); from 2 hours it rebuilt the ROCm stack (a full rebuild). The test image always builds, so it has no cache classes.
  </p>
  <p data-testid="docker-method">
    Window {{ shortDate(payload.window?.from) }} to {{ shortDate(payload.window?.to) }}. Schema v{{ value(payload.schema_version) }}, generated
    {{ shortDate(payload.generated_at) }}. Stale after 6 hours.
  </p>
  <div class="flex flex-wrap gap-2 pt-1">
    <Button variant="outline" size="sm" as="a" :href="LIVE" target="_blank" rel="noopener">Live data <ExternalLink /></Button>
    <Button variant="outline" size="sm" as="a" :href="PAGES" target="_blank" rel="noopener">Pages fallback <ExternalLink /></Button>
  </div>
</template>
