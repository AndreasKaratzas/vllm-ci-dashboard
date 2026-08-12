# vLLM Dashboard Scripts

Additional data collection scripts specific to the vLLM CI dashboard.

## Scripts

| Script | Purpose | Trigger |
|--------|---------|---------|
| `collect_queue_snapshot.py` | Captures Buildkite queue state from cluster metrics + active jobs, records scheduled-job sample coverage against queue counts, and excludes >4h zombie jobs from reconstructed latency analytics | Every 10 min via `queue-monitor.yml`, plus canonical `hourly-master.yml` runs |
| `collect_analytics.py` | Builds failure rankings, duration rankings, queue wait stats | Hourly via `hourly-master.yml` |
| `collect_amd_test_matrix.py` | Normalizes upstream `test-amd.yaml` into a dynamic per-architecture coverage matrix, matched against the latest AMD nightly | Hourly via `hourly-master.yml` |
| `collect_ownership_parity.py` | Builds the ownership routing map from the exact vLLM commit referenced by the latest AMD matrix | Every 30 min after matrix collection |
| `collect_gating_targets.py` | Regenerates `gating_targets.json` from the authoritative `config/vllm_amd_gating_targets.json` | Every canonical `hourly-master.yml` run |
| `collect_gating_proposals.py` | Finds recent open PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors, then follows cached proposal PRs until they stop adding mirrors | Hourly via `hourly-master.yml` |
| `collect_gating_target_candidates.py` | Builds a review-only audit of upstream nightly GPU jobs vs the canonical AMD gating target list, including likely duplicates, exclusions, new candidates, and explicit `%N` shard aggregation | Hourly via `hourly-master.yml` |
| `build_operations_snapshot.py` | Builds the private v2 operations input plus its public manifest and lazy section shards; runtime targets resolve through exact matrix aliases and definition parity with explicit unresolved reasons | Every canonical collection and Pages assembly |
| `build_queue_section.py` | Builds only the compact public Queue shard from queue-owned inputs | Every independent queue-monitor run |
| `ci_main_failure_watcher.py` | Reconciles one upstream `ci`/`main` failure issue and retains bisect candidate bounds per strict group | Hourly after analytics collection |
| `ci_area_regression_watcher.py` | Maps every exact AMD matrix definition to its owned test-area rotation and reconciles one state-owned dashboard issue per regressing area | Hourly after matrix collection |
| `ensure_ci_operations_labels.py` | Ensures managed and workstream labels exist before issue watchers run | Every canonical collection |
| `sync_ci_operations_project.py` | Adds open managed dashboard issues to the linked AMD CI Operations Project, split by workstream labels | Hourly after issue reconciliation |
| `audit_dashboard_data.py` | Cross-checks generated data, frontend assumptions, and deploy workflow ordering before publishing | Hourly via `hourly-master.yml` + local debugging |
| `select_publication_surfaces.py` | Validates collected source transactions, restores only failed surfaces from the captured main baseline, then rebuilds and re-audits the combined snapshot | Every canonical `hourly-master.yml` run before tests |
| `config_parity.py` | Compares AMD vs NVIDIA CI config (commands, test lists) | Part of `collect_ci.py` |
| `pipelines.py` | Pipeline definitions (slug, name patterns, build filters) | Imported by other scripts |

## Environment

All scripts read the `BUILDKITE_TOKEN` from environment variables. This is managed via GitHub Actions encrypted secrets — never hardcode tokens in source files.

The CI ownership watcher reads its only availability input from the committed
regional working-hour profiles in `config/vllm_ci_ownership.json`. EU follows
09:00–17:00 Serbia time (`Europe/Belgrade`) and NA follows 09:00–17:00 Chicago
time (`America/Chicago`), Monday through Friday. Assignment walks ranks 1→2→3
and falls back to the CI lead when no ranked owner is in hours or the schedule
cannot be evaluated safely.

Ready Tickets reads upstream vLLM Project #39 as public, read-only evidence; it
does not mutate that Project's issues, comments, or fields. Its sole writable
Ready Tickets surface is one automation-owned managed comment on dashboard
issue #255, created or updated with the repository-scoped `GITHUB_TOKEN` and
`issues: write`.

Project synchronization uses the repository `GITHUB_TOKEN` only to list the
dashboard's managed issues. The separate `PROJECTS_WRITE_TOKEN` secret is used
only for the `addProjectV2ItemById` mutation against the configured Project.
Missing project credentials are a safe no-op; the script never removes Project
items, edits issue content, or targets another repository.

For queue monitoring specifically, the token needs Buildkite GraphQL access so `collect_queue_snapshot.py` can read cluster queue metrics and scheduled jobs. A dedicated replacement token should be read-only (`read_builds` and `read_clusters`; plus GraphQL access). If GraphQL is unavailable, the collector falls back to the legacy active-build scan.

Buildkite's queue-native p50/p95 remain the site-comparable primary values whenever they are available. The fully paginated scheduled-job reconstruction is stored and charted separately, with exact non-zombie n/N coverage, because equal counts do not prove that two sequential reads contain the same jobs or use the same percentile estimator. Queue history keeps every poll for 48 hours, then retains one actual snapshot plus every queue's primary and reconstructed p50/p95/p99 peaks and exact observation times per UTC hour for the remainder of the 30-day window.

The frequent collector force-publishes a single-commit `queue-data` branch containing only queue-owned evidence and a compact chart feed. The browser compares its current snapshot with the canonical Pages shard, uses the newer one, and falls back to the Pages history if the dedicated feed becomes stale. The verbose JSONL remains available as drill-down evidence but is not reparsed on every chart refresh.

## Data Flow

```
Buildkite API
    |
    v
collect_queue_snapshot.py --> data/vllm/ci/queue_timeseries.jsonl
                          --> data/vllm/ci/queue_jobs.json
build_queue_section.py    --> data/vllm/ci/operations_v2/queue.json
                          --> data/vllm/ci/queue_history_chart.json
                          --> queue-data branch (live browser feed)
collect_capacity_monitor.py --> data/vllm/ci/capacity_monitor.json
collect_analytics.py      --> data/vllm/ci/analytics.json
                           --> ci_main_failure_watcher.py
                           --> open_ci_main_failure_issues.json (private state)
collect_amd_test_matrix.py --> data/vllm/ci/amd_test_matrix.json
collect_ownership_parity.py --> data/vllm/ci/ownership_config_parity.json
collect_gating_targets.py --> data/vllm/ci/gating_targets.json
collect_gating_proposals.py --> data/vllm/ci/gating_proposals.json
collect_gating_target_candidates.py --> data/vllm/ci/gating_target_candidates.json
config_parity.py          --> data/vllm/ci/config_parity.json
amd_test_matrix.json + config_parity.json + ownership_config_parity.json
                         + config/vllm_ci_ownership.json
                           --> ci_area_regression_watcher.py
                           --> ci_ownership.json
                           --> open_ci_area_regression_issues.json (private state)
managed dashboard issues  --> sync_ci_operations_project.py
                           --> AMD CI Operations Project
raw operational inputs    --> build_operations_snapshot.py
                           --> operations_v2.json (private build input)
                           --> operations_v2_manifest.json + operations_v2/*.json
                           --> docs/assets/js/ops-v2.js
audit_dashboard_data.py   --> validates data/ + docs/assets/js + workflows
```

## Bounded last-known-good publication

The hourly workflow treats CI, queue, lifecycle, agent-health, GitHub-home,
Ready Tickets, perf-eval, and test-build inputs as separate atomic publication
surfaces. A collector failure or a routed audit error rejects that surface's
entire candidate transaction. The selector restores the whole surface from the
main commit captured before collection, rebuilds the derived Operations data,
and runs the complete audit again. Unknown findings, code or workflow defects,
an invalid baseline, and any post-restore error remain hard deployment stops.

Fallback state is committed privately in
`data/vllm/ci/publication_state.json`; the public manifest excludes it. The
state binds restored paths to their byte size and SHA-256, retains the first
degraded timestamp across runs, and expires after 36 hours. A degraded
publication keeps its fingerprinted CI incident open even when pytest passes,
while repeated runs of the same incident do not post duplicate comments.

Ready Tickets and its CI ownership subview are guarded by a freshly verified
GitHub PAT in the browser, and their renderers do not fetch protected-view data
before that check passes. Because the repository and Pages deployment are
public static hosting, this is an application boundary rather than
server-enforced confidentiality; private payloads require an authenticated
backend or private hosting.

Runtime target matching is intentionally conservative. Exact build-pinned matrix
labels win; definition-parity aliases are the fallback. Only an explicit
trailing `%N` target can aggregate numbered shards. Duplicate matrix identities
merge incident-first and retain each job URL. Unmatched targets publish a
resolution status (`no_amd_definition`, `stale_target_alias`, `ambiguous`, or
`not_observed`) instead of presenting every identity failure as missing runtime
signal.

The area regression watcher uses all exact matrix definition rows, not the
smaller reviewed runtime-target plan. Area attribution prefers commit-pinned
definition-parity source files, then reviewed aliases/overrides. Ambiguous or
unmapped rows remain unassigned and visible; they are never routed through a
lossy category guess. Issue assignment walks ranks 1→2→3, verifies repository
assignability, and falls back to the CI lead. Every regression issue tags the
selected owner and verified assignee, then CCs each remaining ranked area owner
once. The shared issue client rejects any repository other than the dashboard.

`scripts/build_site.py --cache-bust-index` assembles `docs/` and `data/`
into `_site/` using `config/public_data_manifest.json`; unlisted collector
state and the compatibility monolith are not published. The canonical Pages
deployment replaces `gh-pages`, which removes retired artifacts.
