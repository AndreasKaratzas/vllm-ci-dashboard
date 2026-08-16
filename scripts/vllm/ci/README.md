# vLLM CI Dashboard Backend

Collects nightly CI test data from Buildkite, analyzes test health, and produces JSON files for the project dashboard.

## What It Does

1. **Fetches nightly builds** from two Buildkite pipelines:
   - **AMD** (`amd-ci`): "AMD Full CI Run - nightly" builds (~09:00 UTC / 4 AM Central during daylight time)
   - **Upstream** (`ci`): "Full CI run - nightly" builds (~06:00 UTC / 1 AM Central during daylight time)

2. **Parses pytest output** from job logs to extract test results (pass/fail/skip/error counts + individual failure names from the `short test summary info` section)

3. **Analyzes test health** across builds:
   - Labels each test: `passing`, `failing`, `new_failure`, `fixed`, `flaky`, `skipped`, `new_test`
   - Detects flaky tests (20-80% pass rate over 10-build window)
   - Tracks failure streaks and mean time to fix

4. **Compares AMD vs upstream** (parity analysis):
   - Tests passing on both, failing on both, AMD-only failures, upstream-only, etc.
   - Per-module parity breakdown

5. **Generates dashboard JSON** files consumed by the frontend

## Setup

### Prerequisites

- Python 3.10+
- `requests` and `pyyaml` packages
- Buildkite API token with **read_builds**, **read_artifacts**, and
  **read_clusters** scopes. Queue lifecycle collection needs `read_builds` for
  organization-wide build cohorts and `read_clusters` for the exact queue UUID
  allowlist.
- If you run `collect_queue_snapshot.py`, the token should also have **Enable GraphQL API Access** so queue-native cluster metrics can be read

### Install

```bash
pip install requests pyyaml
```

### Environment

The `BUILDKITE_TOKEN` environment variable must be set. This is managed via GitHub Actions secrets — see the repo Settings > Secrets page. Never commit tokens to the repository.

```bash
# For local development only — use a read-only token
export BUILDKITE_TOKEN="$YOUR_TOKEN"
```

### Run

```bash
# Collect last 7 days (default)
python scripts/collect_ci.py --days 7 --output data/vllm/ci/

# Daily incremental
python scripts/collect_ci.py --days 1

# Dry run (preview builds without fetching)
python scripts/collect_ci.py --dry-run

# Single pipeline only
python scripts/collect_ci.py --pipeline amd --days 3

# Skip analysis (only collect raw data)
python scripts/collect_ci.py --days 7 --skip-analysis
```

## Output Files

All files are written to `data/vllm/ci/`:

| File | Description |
|------|-------------|
| `ci_health.json` | Overall health metrics, build summaries, pass rate trends |
| `parity_report.json` | AMD vs upstream test-by-test comparison |
| `flaky_tests.json` | Registry of flaky tests with pass rates and history |
| `failure_trends.json` | Top offenders, new failures, recently fixed, MTTF |
| `quarantine.json` | Rendered quarantine/allowlist state |
| `test_results/{date}_{pipeline}.jsonl` | Per-test results (one JSON per line) |

### JSONL Format (test_results)

Each line in a `.jsonl` file is a JSON object:
```json
{"test_id":"tests.test_llm::test_generate","name":"test_generate","classname":"tests.test_llm","status":"passed","duration_secs":12.5,"failure_message":"","job_name":"Basic Correctness","job_id":"abc123","build_number":5500,"pipeline":"amd-ci","date":"2026-03-22"}
```

## Test Health Labels

| Label | Criteria | Meaning |
|-------|----------|---------|
| `passing` | >= 80% pass rate over 10 builds | Reliably passing |
| `failing` | <= 20% pass rate over 10 builds | Consistently failing |
| `new_failure` | Was passing (>80%), now failing | Regression detected |
| `fixed` | Was failing, now passing (>80%) | Recently resolved |
| `flaky` | 20-80% pass rate over 10 builds | Intermittent |
| `skipped` | Always skipped/xfailed | Not executing |
| `new_test` | Appeared in <= 2 builds | Too new to classify |
| `quarantined` | Listed in quarantine.yaml | Excluded from metrics |
| `allowlisted` | Listed in allowlist | Known acceptable failure |

## CI Health gated-group percentage

The CI Health headline is one domain-aware gated-group percentage from
`amd_test_matrix.json`, not a percentage of raw YAML jobs. Generic
cross-architecture replicas share one gate and pass when any represented AMD
architecture passes. A reviewed set of MI355-sensitive model, topology, and
kernel routes remains separate because another architecture cannot prove that
gfx950-specific obligation healthy. The exact rules, reasons, membership, and
commands are published in `best_hardware_policy` and `health_groups`.

The denominator contains every expected gate, including waiting or unobserved
groups; missing signal cannot improve the percentage. Raw hardware cells remain
available as drill-down evidence but do not create a second headline metric.
Commit-pinned AMD/upstream definition parity is a separate source-coverage
inventory and does not affect runtime gate health.

## Managing Quarantine

Edit `config/quarantine.yaml` to quarantine or allowlist tests:

```yaml
quarantine:
  - test_id: "tests.test_module::test_name"
    reason: "Known MI325 memory issue"
    issue: "https://github.com/vllm-project/vllm/issues/12345"
    added: "2026-03-01"
    expires: "2026-04-01"    # auto-removes after this date

allowlist:
  - test_id: "tests.test_other::test_unsupported_op"
    reason: "Uses CUDA-specific op not available on ROCm"
    permanent: true
```

Quarantined tests are still collected and tracked, but excluded from failure counts and health metrics.

## GitHub Actions Integration

Five workflows divide canonical publication from focused manual/event collectors:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `hourly-master.yml` | Hourly + Buildkite nightly-completion webhooks | Full collection, validation, and the only scheduled root-site deployment |
| `daily-update.yml` | Manual | Focused GitHub-data refresh committed to `main` |
| `ci-collect.yml` | Manual | Validation-only focused Buildkite CI refresh; never commits or publishes |
| `queue-monitor.yml` | Queue webhooks + manual | Queue snapshots and bounded queue issue automation; canonical publication follows via `hourly-master.yml` |
| `queue-lifecycle.yml` | Hourly + manual | Organization-wide direct job lifecycle observations for the twelve canonical MI250/MI300/MI355 queues |

All secrets are managed via GitHub Actions encrypted secrets (Settings > Secrets > Actions). The `BUILDKITE_TOKEN` is never exposed in logs — GitHub automatically masks secret values.

### Webhook-Triggered Updates

For build-completion updates, `hourly-master.yml` receives the
`buildkite_build_finished` repository dispatch and performs a complete,
validated publication.

Buildkite queue freshness now uses those job-level webhook events (`job.scheduled`, `job.started`, `job.finished`) plus agent events (`agent.connected`, `agent.disconnected`, `agent.lost`, `agent.stopping`) to dispatch the lightweight `queue-monitor.yml` workflow. This keeps queue counts and zombie-job alerts fresher without forcing the heavier CI collectors to run on every queue change.

Queue analytics intentionally exclude waiting or running jobs older than 4 hours. Those jobs are treated as zombies, surfaced separately in `queue_jobs.json`, and tracked via `queue_zombie_watcher.py` so the main queue charts stay conservative.

Canonical AMD lifecycle analytics are intentionally narrower than the general
queue monitor. `collect_queue_lifecycle.py` queries only the twelve standard
`amd_mi250_*`, `amd_mi300_*`, and `amd_mi355_*` queues at widths 1, 2, 4, and
8. MI325, MI355B, CPU, partner, perf-eval, and router queues are excluded from
these totals. The collector records three event-time facts independently:
incoming work at REST `build.jobs[].runnable_at`, served work at
`build.jobs[].started_at`, and completions at `build.jobs[].finished_at`. It
derives queue wait and runtime only when both required source
timestamps are present, and publishes exact observed rolling two-hour counts
plus UTC hour buckets in `queue_lifecycle.json`. The supported organization
Builds REST endpoint does not filter job event timestamps directly. The
collector unions builds finished inside the source window, builds created
inside it, and active-state builds created inside the bounded parent-build
horizon, then filters jobs by the twelve direct cluster-queue UUIDs. Every
retained value is therefore an exact direct job observation, while the
aggregate separately declares residual population limits such as page-number
drift and jobs attached to parent builds created before that horizon.

The reconciled, deduplicated seven-day job-observation ledger lives only on the
`queue-lifecycle-data` branch as daily files under `queue_lifecycle_jobs/`; it
is neither committed to `main` nor published to Pages. Each compact row contains a hashed
job identity, its canonical queue, direct event timestamps, derived durations,
outcome, and retry flags. UTC-day segmentation lets unchanged days reuse their
existing Git objects instead of rewriting the whole ledger; a late start or
completion can still update the segment containing that job's earliest retained
event. The ledger deliberately omits labels, URLs, branches,
commits, pipeline names, and other build metadata. Per-segment and total-size
guards prevent GitHub blob-limit and repository-pressure failures.

Lifecycle collection runs independently once per hour so an API or schema
failure cannot delay the ten-minute point-in-time queue monitor. Organization-
wide finished, created, and active-build cohorts use bounded, verified REST
pagination, and the public aggregate includes the exact source window, cohort
filters, query coverage, and provenance. Incomplete pagination, an unreadable
established ledger, or a failed Buildkite query causes collection to fail
instead of silently publishing a partial window. Workflows pass the existing
`BUILDKITE_TOKEN` secret to the collector as
`BUILDKITE_API_TOKEN`; tokens must never be placed in source, generated data,
logs, or dashboard URLs.

### Managed Alert Issues

The unified hourly Data Collection workflow also reconciles four bounded
umbrella issues in this repository:

- AMD main test-group failures use the exhaustive amd-ci branch=main reliability
  cohort. The latest retry attempt in a build wins; a later pass resolves the
  same strict label + step + hardware + queue identity. Hard failures confirm
  immediately. Soft failures remain visible as pending observations and require
  two distinct eligible completed builds before becoming incidents. Missing or
  indeterminate observations neither advance nor resolve the signal.
- Upstream CI main test-group failures use the exhaustive ci branch=main
  reliability cohort and the same strict retry-aware identity. Each active
  incident retains the last known passing commit and first failing commit as a
  candidate range for later ancestry validation and automated git bisection.
- AMD main duration regressions compare the median wall completion time of the
  latest three successful final attempts with the preceding six to twelve runs.
  Queue wait is excluded. A 15% increase opens the alert, and the baseline stays
  fixed until the latest-three median recovers below that threshold.
- CI agent health uses the dashboard's infra-suspect definition, excludes
  canceled builds and unidentified nodes, and alerts when a six-hour window
  contains a three-hour co-failure cluster with at least three logical failures
  across at least two groups on one physical AMD node.

State lives in open_amd_main_failure_issues.json,
open_ci_main_failure_issues.json, open_amd_duration_regression_issues.json, and
open_agent_health_issues.json.
Each watcher can update or close only the issue number in its own state file; it
never searches for issues by label, and it verifies a watcher-specific ownership
marker before any update or close. When a signal recovers, its watcher comments
with the recovery rule and automatically closes that tracked issue. Manually
closing an active alert suppresses reopening until that signal first recovers.

## Architecture

```
scripts/
  collect_ci.py              # Entry point / orchestrator
  ci/
    config.py                # Constants, thresholds, pipeline definitions
    models.py                # Dataclasses: TestResult, BuildSummary, TestHealth, ParityEntry
    buildkite_client.py      # Buildkite REST API client
    log_parser.py            # Pytest log output parser (extracts test results from job logs)
    junit_parser.py          # JUnit XML parser (fallback if artifacts are available)
    analyzer.py              # Health labeling, parity, trends, quarantine
    reporter.py              # JSON/JSONL output generation
    webhook.py               # Standalone Buildkite webhook receiver
```

## Troubleshooting

**"BUILDKITE_TOKEN not set"**: Ensure the token is configured in GitHub Actions secrets or exported in your local environment.

**No nightly builds found**: The script filters by build name pattern. Check that the pipeline has builds matching "AMD Full CI Run - nightly" or "Full CI run - nightly".

**Rate limiting (429)**: The script retries on 429 with exponential backoff using the `Retry-After` header. For large fetches (30+ days), run in smaller batches: `--days 7`.

**Cached data**: Build data is cached in `data/vllm/ci/.cache/`. JSONL test results are also cached — the script skips builds that already have results. Delete the cache to force a full re-fetch.
