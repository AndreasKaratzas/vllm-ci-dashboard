# Dashboard Audit

This is the checklist for checking whether the dashboard is telling the same
story across Home, Test Parity, CI Health, CI Analytics, Queue Monitor, and the
GitHub Pages deploy path.

Run the automated pass:

```bash
python scripts/vllm/audit_dashboard_data.py
```

The audit is intentionally local and deterministic. It reads committed data,
frontend JS, and workflow YAML. It does not call GitHub or Buildkite.

## Source Of Truth

| Surface | Source file | Producer | Primary consumer |
|---------|-------------|----------|------------------|
| Home PRs | `data/vllm/prs.json` | `scripts/collect.py` | `docs/assets/js/dashboard.js` |
| Home project issues | `data/vllm/issues.json` | `scripts/collect.py` | `docs/assets/js/dashboard.js` |
| CI Health | `data/vllm/ci/ci_health.json` | `scripts/collect_ci.py` | `docs/assets/js/ci-health.js` |
| Parity/Home hardware breakdown | `data/vllm/ci/parity_report.json` | `scripts/collect_ci.py` | `docs/assets/js/dashboard.js` |
| CI Analytics | `data/vllm/ci/analytics.json` | `scripts/vllm/collect_analytics.py` | `docs/assets/js/ci-analytics.js` |
| AMD HW Matrix | `data/vllm/ci/amd_test_matrix.json` | `scripts/vllm/collect_amd_test_matrix.py` | `docs/assets/js/ci-analytics.js` |
| Queue charts | `data/vllm/ci/queue_timeseries.jsonl` | `scripts/vllm/collect_queue_snapshot.py` | `docs/assets/js/ci-queue.js` |
| Queue overlays | `data/vllm/ci/queue_jobs.json` | `scripts/vllm/collect_queue_snapshot.py` | `docs/assets/js/ci-queue.js` |

## Automated Checks

- Every high-value data file exists, parses, and has the keys its view reads.
- Linked project #39 issues and CI PR tags agree both ways.
- CI Health latest build numbers match the latest parsed JSONL files.
- CI Analytics has non-empty windows, recent builds, failure rankings, duration rankings, and chartable build rows.
- AMD HW Matrix summary totals are recomputed from its rows.
- AMD HW Matrix links point at the matrix source build, not an older nightly.
- Home parity hardware counts agree with the AMD HW Matrix per architecture.
- Queue totals equal the per-queue sums, and the default 72h AMD workload is nonzero.
- Frontend tokens that encode key UX decisions still exist: 10-row tables, overall score bar, wider hardware bars, CI Analytics matrix copy, and Queue Monitor defaulting to running workload.
- Every Pages writer shares the `gh-pages-deploy` lock and uses `scripts/build_site.py --cache-bust-index`.
- `hourly-master.yml` runs the audit after data generation and before deploy.

## Manual Spot Checks

- Open the latest AMD nightly from `data/vllm/ci/amd_test_matrix.json.source.latest_build_url` and compare the Buildkite canvas failure count with the matrix `failing_cells`.
- On Home, click each AMD hardware row and check that the failing group count matches CI Analytics -> AMD HW Matrix for the same architecture.
- In CI Analytics, confirm Recent Builds, Test Group Trends, Top Failures, Slowest Jobs, and Job Pass Rate charts are populated for both AMD CI and Upstream CI.
- In Queue Monitor, keep the default metric on Running and confirm the 72h chart shows actual AMD nightly workload even when Waiting is zero.
- After a workflow deploy, inspect the `gh-pages` branch for conflict markers in `data/**/*.json` and `data/**/*.jsonl`.
