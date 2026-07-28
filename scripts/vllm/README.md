# vLLM Dashboard Scripts

Additional data collection scripts specific to the vLLM CI dashboard.

## Scripts

| Script | Purpose | Trigger |
|--------|---------|---------|
| `collect_queue_snapshot.py` | Captures Buildkite queue state from cluster metrics + active jobs, prunes pre-fix history, and excludes >4h zombie jobs from queue analytics | Every 30 min via `hourly-master.yml`, plus manual/webhook `queue-monitor.yml` runs |
| `collect_analytics.py` | Builds failure rankings, duration rankings, queue wait stats | Every 30 min via `hourly-master.yml` |
| `collect_amd_test_matrix.py` | Normalizes upstream `test-amd.yaml` into a dynamic per-architecture coverage matrix, matched against the latest AMD nightly | Every 30 min via `hourly-master.yml` |
| `collect_ownership_parity.py` | Builds the ownership routing map from the exact vLLM commit referenced by the latest AMD matrix | Every 30 min after matrix collection |
| `collect_gating_targets.py` | Regenerates `gating_targets.json` from the authoritative `config/vllm_amd_gating_targets.json` | Every canonical `hourly-master.yml` run |
| `collect_gating_proposals.py` | Finds recent open PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors, then follows cached proposal PRs until they stop adding mirrors | Every 30 min via `hourly-master.yml` |
| `collect_gating_target_candidates.py` | Builds a review-only audit of upstream nightly GPU jobs vs the canonical AMD gating target list, including likely duplicates, exclusions, new candidates, and explicit `%N` shard aggregation | Every 30 min via `hourly-master.yml` |
| `build_operations_snapshot.py` | Builds the private v2 operations input plus its public manifest and lazy section shards; runtime targets resolve through exact matrix aliases and definition parity with explicit unresolved reasons | Every canonical collection and Pages assembly |
| `ci_area_regression_watcher.py` | Maps every exact AMD matrix definition to its owned test-area rotation and reconciles one state-owned dashboard issue per regressing area | Every 30 min after matrix collection |
| `ensure_ci_operations_labels.py` | Ensures managed and workstream labels exist before issue watchers run | Every canonical collection |
| `sync_ci_operations_project.py` | Adds open managed dashboard issues to the linked AMD CI Operations Project, split by workstream labels | Every 30 min after issue reconciliation |
| `audit_dashboard_data.py` | Cross-checks generated data, frontend assumptions, and deploy workflow ordering before publishing | Every 30 min via `hourly-master.yml` + local debugging |
| `config_parity.py` | Compares AMD vs NVIDIA CI config (commands, test lists) | Part of `collect_ci.py` |
| `pipelines.py` | Pipeline definitions (slug, name patterns, build filters) | Imported by other scripts |

## Environment

All scripts read the `BUILDKITE_TOKEN` from environment variables. This is managed via GitHub Actions encrypted secrets — never hardcode tokens in source files.

The CI ownership watcher additionally reads `CI_OWNER_AVAILABILITY_JSON` from
an encrypted Actions secret. Its schema is documented in the root README.
Working hours and PTO never enter generated public data or managed issue state;
individual availability states are not rendered. Missing or stale availability
escalates to the CI lead.

Project synchronization uses the repository `GITHUB_TOKEN` only to list the
dashboard's managed issues. The separate `PROJECTS_WRITE_TOKEN` secret is used
only for the `addProjectV2ItemById` mutation against the configured Project.
Missing project credentials are a safe no-op; the script never removes Project
items, edits issue content, or targets another repository.

For queue monitoring specifically, the token should also have Buildkite GraphQL access enabled so `collect_queue_snapshot.py` can read cluster queue metrics (`connected_agents`, `waiting`, `running`). If GraphQL access is unavailable, the collector falls back to the legacy active-build scan.

Queue history is automatically pruned to the post-fix reset epoch declared in `vllm.constants`, so older snapshots from the pre-fix collector do not re-enter the dashboard via `gh-pages` sync.

## Data Flow

```
Buildkite API
    |
    v
collect_queue_snapshot.py --> data/vllm/ci/queue_timeseries.jsonl
                          --> data/vllm/ci/queue_jobs.json
collect_capacity_monitor.py --> data/vllm/ci/capacity_monitor.json
collect_analytics.py      --> data/vllm/ci/analytics.json
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
assignability, and falls back to the CI lead. All issue bodies are mention-free
and the shared issue client rejects any repository other than the dashboard.

`scripts/build_site.py --cache-bust-index` assembles `docs/` and `data/`
into `_site/` using `config/public_data_manifest.json`; unlisted collector
state and the compatibility monolith are not published. The canonical Pages
deployment replaces `gh-pages`, which removes retired artifacts.
