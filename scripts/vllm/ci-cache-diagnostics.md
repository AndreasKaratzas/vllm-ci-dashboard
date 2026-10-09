# One-build cache diagnostics

Run **CI Cache Diagnostics** manually on `main` to inspect one already projected
AMD MI build without recollection. Supply an exact existing Actions cache key,
the CI main build number and its full immutable commit. For the failed full run
37986924810, the inputs are:

```text
cache_key: analytics-builds-v1-Linux-2026-10-09-37986924810-1
build_number: 93775
full_commit: ad73a4740dd780c5620099261738a30b979b262c
```

The workflow restores that key without fallback and validates the production
cache schema, digests, declared coverage and original 48-hour freshness limit.
It requires the selected build's existing MI execution scope, exact commit/tree
and immutable CPU-routing index. CPU-only and foreign jobs reject the export.
The diagnostic uses the saved index locally and makes no source API requests.

The one-day artifact contains only `build.json` and `receipt.json`, together at
most 2 MiB. Job IDs, labels, queues, step IDs/keys, retries and original timestamps
come from the privacy-filtered cache. The receipt binds the exact cache key,
manifest integrity and byte digest, source index, and selected build byte digest
and Git blob ID. Cache and source clocks remain unchanged. Local output files
use mode 600 in a new directory outside the checkout.

This manual diagnostic has read permissions, receives no Buildkite secrets,
saves no cache, and publishes no dashboard generation. A missing, stale,
corrupt, mismatched or oversized selection fails without an artifact.
