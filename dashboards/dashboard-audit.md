# Dashboard audit

Current metrics follow two explicit authorities: source coverage from immutable
upstream `main` CI definitions, and runtime health from the observed `ci` nightly.
Execution metrics include only AMD MI GPU jobs in `ci`. CUDA, NVIDIA, Intel, CPU,
unknown routing, and legacy `amd-ci` results do not contribute to runtime rows or
percentages. Upstream GPU YAML definitions remain a configuration benchmark for
AMD coverage; they are not executed-hardware observations.

| View / metric | Source | Producer |
| --- | --- | --- |
| Command Center / Upstream parity / AMD mirrors | `test_group_parity.json` with current-main source SHA and configured gate flags | `build_test_group_parity.py` |
| AMD runtime / hardware health | `ci_health.json`, `amd_test_matrix.json`, exact nightly source definitions | `collect_ci.py`, `collect_amd_test_matrix.py` |
| Latency | `analytics.json` current five-nightly AMD MI cohort, exact job timing, and each nightly's immutable source families | `collect_analytics.py`, `ci/nightly_latency.py`, `ci/runtime_families.py` |
| Reliability | Current AMD MI `ci` all-main cohort with retained outcome / attempt evidence | `collect_analytics.py` |
| DNS / physical agents | Independently collected infrastructure evidence | DNS and agent-health collectors |
| Omni | Current queue observations, workload mapping, capacity, and exact active jobs | Queue and workload collectors |
| Organization rollups | Schema-v7 `org_summary.json` | Operations snapshot builder |

`audit_dashboard_data.py` verifies current pipeline/hardware scope, source pins,
coverage arithmetic, runtime logical counts, exact evidence links, five-nightly
latency samples, shard durations, medians, retained public sections, and bounded
publication proofs. Global/code defects stop publication; collector failures can
restore a validated last-known-good source transaction within its explicit TTL.
The latency audit also reconstructs each job's definition family from that
build's source catalog and rejects timing rows that merge distinct families,
even when their durations and build membership are internally consistent.

Physical AMD agent-health evidence also declares its current `ci` scope and
exhaustive job-start interval. Older retained observations do not extend this
proof. Date selections outside the current proved interval retain observed
counts and links, with exact failure percentages unavailable.

For release review, run deterministic pytest, lint/type checks, browser smoke
checks, and a full assembled-bundle synthetic probe. After guarded canonical
collection, verify the live source SHAs, latency sample dates, parity count,
removed routes, exact build links, and publication status. See
[Dashboard Cleanup](dashboard-cleanup.md) for the removed-module inventory and
retained shared dependencies.
