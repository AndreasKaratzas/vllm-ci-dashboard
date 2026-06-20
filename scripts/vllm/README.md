# vLLM Dashboard Scripts

Additional data collection scripts specific to the vLLM CI dashboard.

## Scripts

| Script | Purpose | Trigger |
|--------|---------|---------|
| `collect_queue_snapshot.py` | Captures Buildkite queue state from cluster metrics + active jobs, prunes pre-fix history, and excludes >4h zombie jobs from queue analytics | Hourly via `queue-monitor.yml` + Buildkite queue webhooks |
| `collect_analytics.py` | Builds failure rankings, duration rankings, queue wait stats | Part of `collect_ci.py` |
| `collect_amd_test_matrix.py` | Normalizes upstream `test-amd.yaml` into a dynamic per-architecture coverage matrix, matched against the latest AMD nightly | Hourly via `hourly-master.yml` |
| `collect_gating_proposals.py` | Finds recent open PRs from tracked AMD engineers that add new `.buildkite/test_areas` AMD mirrors, then follows cached proposal PRs until they stop adding mirrors | Hourly via `hourly-master.yml` |
| `collect_gating_target_candidates.py` | Builds a review-only audit of upstream nightly GPU jobs vs the canonical AMD gating target list, including likely duplicates, exclusions, and new candidates | Hourly via `hourly-master.yml` |
| `audit_dashboard_data.py` | Cross-checks generated data, frontend assumptions, and deploy workflow ordering before publishing | Hourly via `hourly-master.yml` + local debugging |
| `collect_activity.py` | Engineer activity profiles and contribution scoring | Part of `daily-update.yml` |
| `config_parity.py` | Compares AMD vs NVIDIA CI config (commands, test lists) | Part of `collect_ci.py` |
| `pr_scoring.py` | Scores PRs by importance (area, size, impact) | Part of `daily-update.yml` |
| `pipelines.py` | Pipeline definitions (slug, name patterns, build filters) | Imported by other scripts |

## Environment

All scripts read the `BUILDKITE_TOKEN` from environment variables. This is managed via GitHub Actions encrypted secrets — never hardcode tokens in source files.

For queue monitoring specifically, the token should also have Buildkite GraphQL access enabled so `collect_queue_snapshot.py` can read cluster queue metrics (`connected_agents`, `waiting`, `running`). If GraphQL access is unavailable, the collector falls back to the legacy active-build scan.

Queue history is automatically pruned to the post-fix reset epoch declared in `vllm.constants`, so older snapshots from the pre-fix collector do not re-enter the dashboard via `gh-pages` sync.

## Data Flow

```
Buildkite API
    |
    v
collect_queue_snapshot.py --> data/vllm/ci/queue_timeseries.jsonl
collect_analytics.py      --> data/vllm/ci/analytics.json
collect_amd_test_matrix.py --> data/vllm/ci/amd_test_matrix.json
collect_gating_proposals.py --> data/vllm/ci/gating_proposals.json
collect_gating_target_candidates.py --> data/vllm/ci/gating_target_candidates.json
collect_activity.py       --> data/vllm/engineer_activity.json
config_parity.py          --> data/vllm/ci/config_parity.json
pr_scoring.py             --> data/vllm/pr_scores.json
audit_dashboard_data.py   --> validates data/ + docs/assets/js + workflows
```

`scripts/build_site.py --cache-bust-index` assembles `docs/` and `data/`
into `_site/`, then the Pages workflows publish `_site/` to `gh-pages`.
