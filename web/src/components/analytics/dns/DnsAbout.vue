<script setup lang="ts">
import { ExternalLink } from '@lucide/vue'
import { Button } from '@/components/ui/button'
import { DNS_FALLBACK_ASSET, queueDnsSourceLabel, type DnsPresentation } from '@/domain/dns'
import { shortDate, value, type Rec } from '@/domain/format'

defineProps<{ payload: Rec; view: DnsPresentation }>()
</script>

<template>
  <p>
    A job attempt counts once when its complete log contains a DNS-specific resolver signature. Repeated matching lines collapse into episodes;
    retries remain distinct attempts. Generic connection, TLS, timeout, and HTTP failures do not count.
  </p>
  <p>
    Outcomes are the final Buildkite job state after the observation. They are correlation, not proof that DNS caused the result.
  </p>
  <p>
    Canonical AMD is the 12 standard MI250, MI300, and MI355 queues at widths 1, 2, 4, and 8. All active AMD GPU also includes other amd_mi* models
    and widths, such as MI325; retired MI355B queues are excluded.
  </p>
  <p v-if="view.block" data-testid="dns-method">
    Window {{ shortDate(view.block.start) }} to {{ shortDate(view.block.end_exclusive) }}. Schema v{{ value(payload.schema_version) }}, generated
    {{ shortDate(payload.generated_at) }} from the {{ queueDnsSourceLabel(payload.__sourceAsset) }}.
  </p>
  <p v-if="view.coverage.notes.length">{{ view.coverage.notes.join('; ') }}.</p>
  <div class="flex flex-wrap gap-2 pt-1">
    <Button variant="outline" size="sm" as="a" :href="payload.__sourceAsset || DNS_FALLBACK_ASSET" target="_blank" rel="noopener">
      Selected data <ExternalLink />
    </Button>
    <Button variant="outline" size="sm" as="a" :href="DNS_FALLBACK_ASSET" target="_blank" rel="noopener">Pages fallback <ExternalLink /></Button>
  </div>
</template>
