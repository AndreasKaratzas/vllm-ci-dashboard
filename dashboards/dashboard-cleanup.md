# Dashboard view retirement and current CI authority

The dashboard measures current AMD and CUDA GPU workloads in Buildkite `ci`.
Legacy `amd-ci` runs are excluded from current health, parity percentages, and
latency. Physical node and DNS history remain independent infrastructure evidence.

## Removed modules and artifacts

| Removed surface | Deleted producers / code | Deleted artifacts |
| --- | --- | --- |
| CI Workload Trajectory | `collect_hotness.py`, `collect_group_changes.py`, trajectory renderers and snapshot projections | `hotness.json`, `group_changes.json`, `operations_v2/trajectory.json` |
| Target Health | `collect_gating_targets.py`, `collect_gating_target_candidates.py`, `collect_gating_proposals.py`, `write_gating_nightlies`, target renderers and joins | `vllm_amd_gating_targets.json`, `gating_targets.json`, `gating_target_candidates.json`, `gating_proposals.json`, `gating_nightlies.json`, `operations_v2/gating.json` |
| Flake / Retry Comparison | comparison tables, historical comparison projections, retry-only comparison helpers | `operations_v2/comparison_retry_evidence.json` |
| Queue Monitor page | queue page renderer, page-only helpers, navigation, controls, and CSS | No independent collector retirement: shared observations still serve Omni and infrastructure automation |

Exclusive tests, workflow steps, public manifest entries, storage allocations,
source-audit rules, and CSS were retired with their consumers. Old deep links
redirect to supported views. The still-used hardware identity normalizer moved
from the retired candidate collector into `ci/group_identity.py`.

## Retained contracts

| Capability | Authority and maintenance rule |
| --- | --- |
| Command Center and Upstream parity | Immutable current upstream `main` CI configuration; classification policy contains explicit unsupported groups, never frozen coverage totals |
| AMD mirrors | Inline and native AMD routes from the same current-main source pin; required, optional, and soft-fail source flags stay distinct |
| Runtime AMD health and hardware | AMD GPU jobs in `ci`; definitions pinned to the exact observed nightly commit; any-hardware and all-hardware logical counts are explicit |
| Latency Comparison | Global latest five completed `ci` / `main` nightlies; maximum complete parallel-shard wall minutes per nightly, then median; sample count, dates, and exact jobs shown; no older backfill |
| Reliability / retry drilldowns | Current `ci` cohorts and exact retained attempt evidence; these are still used by incident investigation |
| Omni and infrastructure automation | Shared current queue, capacity, workload, lifecycle, physical agent health, and DNS evidence; retained independently of removed pages |

## Publication and Actions stability

Bundle v3 publishes exactly eleven lazy sections. Its health readers retain
strict bounded support for immutable v1/v2 publications, deployed before writer
activation. Publication surface contract v6 validates complete v5 restore proofs
before removing retired domains and their clocks. Canonical Actions purge retired
artifacts only after validated selection; the public allowlist also forbids them.

Current source parity refreshes without a Buildkite token. Runtime collection
fetches `ci` once and splits by GPU platform. Private roster caches are invalidated
by the schema-v3 current-CI handoff. Request guards, durable attempt budgets,
resumable caches, exact source pins, atomic bounded writes, live audits, browser
checks, publication verification, and synthetic site probes remain enforced.

Historical attempt-ledger evidence retains its exact known retired surface names
so immutable request accounting remains readable. New evidence and retry requests
accept only active collectors. Workflow audits enforce the retained producer steps
and reject references to deleted collectors.

Regressions cover CPU/legacy exclusion, global cohort selection, missing-group
and incomplete-shard behavior, exact five-nightly links, observed agent routing,
current source coverage, retired navigation, malformed legacy restore proofs,
and the complete retained public bundle.

The latency projection retains exact job evidence in a declared column format
when needed to fit its 7 MiB public allocation. A 225-group, five-nightly,
two-platform, eight-shard stress case preserves every sample and round-trips
the evidence exactly; the whole eager bundle still fits its 32 MiB envelope.
