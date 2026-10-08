# Dashboard audit

Current metrics follow two explicit authorities: source coverage from immutable
upstream `main` CI definitions, and runtime health from the observed `ci` nightly.
AMD and CUDA GPU populations are separated; CPU and legacy `amd-ci` jobs do not
contribute to current percentages.

| View / metric | Source | Producer |
| --- | --- | --- |
| Command Center / Upstream parity / AMD mirrors | `test_group_parity.json` with current-main source SHA and configured gate flags | `build_test_group_parity.py` |
| AMD runtime / hardware health | `ci_health.json`, `amd_test_matrix.json`, exact nightly source definitions | `collect_ci.py`, `collect_amd_test_matrix.py` |
| Latency | `analytics.json` current five-nightly cohort and exact job timing | `collect_analytics.py`, `ci/nightly_latency.py` |
| Reliability | Current `ci` all-main cohort with retained outcome / attempt evidence | `collect_analytics.py` |
| DNS / physical agents | Independently collected infrastructure evidence | DNS and agent-health collectors |
| Omni | Current queue observations, workload mapping, capacity, and exact active jobs | Queue and workload collectors |
| Organization rollups | Schema-v7 `org_summary.json` | Operations snapshot builder |

`audit_dashboard_data.py` verifies current pipeline/hardware scope, source pins,
coverage arithmetic, runtime logical counts, exact evidence links, five-nightly
latency samples, shard durations, medians, retained public sections, and bounded
publication proofs. Global/code defects stop publication; collector failures can
restore a validated last-known-good source transaction within its explicit TTL.

For release review, run deterministic pytest, lint/type checks, browser smoke
checks, and a full assembled-bundle synthetic probe. After guarded canonical
collection, verify the live source SHAs, latency sample dates, parity count,
removed routes, exact build links, and publication status. See
[Dashboard Cleanup](dashboard-cleanup.md) for the removed-module inventory and
retained shared dependencies.
