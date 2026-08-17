# Dashboard Audit

This is the checklist for checking whether the dashboard is telling the same
story across Home, Test Parity, CI Health, CI Analytics, Queue Monitor, Omni,
and the GitHub Pages deploy path.

Run the automated pass:

```bash
python scripts/vllm/audit_dashboard_data.py --strict-warnings
```

The audit is intentionally local and deterministic. It reads committed data,
frontend JS, and workflow YAML. It does not call GitHub or Buildkite.

## Source Of Truth

| Surface | Source file | Producer | Primary consumer |
|---------|-------------|----------|------------------|
| Home PRs | `data/vllm/prs.json` | `scripts/collect.py` | `docs/assets/js/dashboard.js` |
| Home project issues | `data/vllm/issues.json` | `scripts/collect.py` | `docs/assets/js/dashboard.js` |
| Home test summary | `data/vllm/test_results.json` | `scripts/collect_ci.py` | `docs/assets/js/dashboard.js` |
| Operations manifest and lazy shards | `data/vllm/ci/operations_v2_manifest.json`, `data/vllm/ci/operations_v2/*.json` | `scripts/vllm/build_operations_snapshot.py` | `docs/assets/js/ops-v2.js` |
| CI Health nightly signal | `data/vllm/ci/ci_health.json`, `data/vllm/ci/analytics.json` | `scripts/collect_ci.py`, `scripts/vllm/collect_analytics.py` | operations snapshot builder |
| CI Health AMD targets | `data/vllm/ci/gating_targets.json`, `data/vllm/ci/gating_target_candidates.json`, `data/vllm/ci/amd_test_matrix.json` | `scripts/vllm/collect_gating_targets.py`, `scripts/vllm/collect_gating_target_candidates.py`, `scripts/vllm/collect_amd_test_matrix.py` | operations snapshot builder |
| Parity/Home hardware breakdown | `data/vllm/ci/parity_report.json` | `scripts/collect_ci.py` | `docs/assets/js/dashboard.js` |
| CI Analytics | private `data/vllm/ci/analytics.json`; bounded public projection at the same site path | `scripts/vllm/collect_analytics.py`; `scripts/vllm/ci/public_analytics.py` during site assembly | private reliability watchers and audit; browser projection |
| AMD HW Matrix | `data/vllm/ci/amd_test_matrix.json` | `scripts/vllm/collect_amd_test_matrix.py` | operations snapshot builder |
| Queue charts | `data/vllm/ci/queue_timeseries.jsonl` | `scripts/vllm/collect_queue_snapshot.py` | operations snapshot builder |
| Queue and Omni active jobs | `data/vllm/ci/queue_jobs.json` | `scripts/vllm/collect_queue_snapshot.py` | operations snapshot builder |

## Automated Checks

- Every high-value data file exists, parses, and has the keys its view reads.
- Linked project #39 issues and CI PR tags agree both ways.
- CI Health latest build numbers match the latest parsed JSONL files, and
  current/latest group counts remain distinct from retained-history counts.
- Pass-rate fields declare their denominator: analytics build percentages use
  terminal build outcomes, while CI-health and Home test percentages use
  passed versus failed pytest assertions and exclude skipped assertions. The
  audit enforces these fields for `pass_rate_contract_version: 1`; unversioned
  pre-rollout data remains readable with a warning until its next collection.
- CI Health target incident lists sort hard failures, soft failures, and
  unobserved targets before passing targets, then alphabetically within state.
- CI Analytics has non-empty windows, recent builds, failure rankings, duration rankings, and chartable build rows.
- AMD HW Matrix summary totals are recomputed from its rows.
- AMD HW Matrix links point at the matrix source build, not an older nightly.
- Home parity hardware counts agree with the AMD HW Matrix per architecture.
- Queue totals equal the per-queue sums, and the default 72h AMD workload is nonzero.
- Omni exact active-job ledger counts remain separate from workload-attributed
  queue aggregates; partial attribution is labeled as a lower bound.
- Omni 1h, 3h, 6h, 12h, 1d, and 3d windows and UTC day-over-day rows use only
  snapshots with explicit queued-workload attribution.
- The operations manifest matches its lazy shard bytes and generated payloads.
- Full CI analytics remains a private, selector-owned build input. The public
  manifest declares its bounded projection, publication-size accounting uses
  that projection's `max_bytes`, and the hourly workflow never restores the
  public projection into the private reliability input.
- The versioned analytics build cache is restored before analytics collection
  from GitHub Actions cache storage only. Its UTC-day key, prior-day fallback,
  post-success save, gitignore rule, and never-publish coverage are audited;
  cache transport failures do not block a fresh Buildkite collection.
- Frontend tokens that encode key UX decisions still exist: 10-row tables, overall score bar, wider hardware bars, CI Analytics matrix copy, and Queue Monitor defaulting to running workload.
- Every Pages writer shares the `gh-pages-deploy` lock and uses `scripts/build_site.py --cache-bust-index`.
- `hourly-master.yml` runs the audit after data generation and before deploy.
- The independent `health-check.yml` canary runs at :57 each hour and on
  demand. It uploads bounded JSON evidence, updates one exact-marker-owned
  issue without hourly comments, requires two consecutive healthy probes to
  close/rearm, and fails only after issue reconciliation. It has read-only
  contents permission and never writes Pages or `main`.

## Manual Spot Checks

- Open the latest AMD nightly from `data/vllm/ci/amd_test_matrix.json.source.latest_build_url` and compare the Buildkite canvas failure count with the matrix `failing_cells`.
- On Home, click each AMD hardware row and check that the failing group count matches CI Analytics -> AMD HW Matrix for the same architecture.
- In CI Analytics, confirm Recent Builds, Test Group Trends, Top Failures, Slowest Jobs, and Job Pass Rate charts are populated for both AMD CI and Upstream CI.
- In Queue Monitor, keep the default metric on Running and confirm the 72h chart shows actual AMD nightly workload even when Waiting is zero.
- In CI Health targets, open MI300 incidents and confirm non-passing states come
  first and “Basic Models Tests (Other)” precedes “e2e Scheduling (1 GPU)”.
- In Omni, compare the exact ledger with the separately labeled attributed
  aggregate, change the history horizon and queued-age band, and verify every
  job row opens its exact Buildkite evidence.
- After a workflow deploy, inspect the `gh-pages` branch for conflict markers in `data/**/*.json` and `data/**/*.jsonl`.
