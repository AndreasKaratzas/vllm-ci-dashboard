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
- Buildkite API token with **read_builds** and **read_artifacts** scopes
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

Three workflows handle automated CI data collection:

| Workflow | Schedule | Purpose |
|----------|----------|---------|
| `daily-update.yml` | Hourly (:45) | Full data collection + site deployment |
| `ci-collect.yml` | Daily (1 PM UTC) + webhook | Dedicated CI data collection from Buildkite |
| `queue-monitor.yml` | Hourly (:15) + queue webhooks | Queue time-series snapshots, queue-latency issues, zombie-job issues |

All secrets are managed via GitHub Actions encrypted secrets (Settings > Secrets > Actions). The `BUILDKITE_TOKEN` is never exposed in logs — GitHub automatically masks secret values.

### Webhook-Triggered Updates

For real-time updates, `ci-collect.yml` can be triggered by Buildkite webhooks via `repository_dispatch`. Configure a Buildkite notification service to POST to the GitHub dispatches API with event type `buildkite_build_finished`.

Buildkite queue freshness now uses those job-level webhook events (`job.scheduled`, `job.started`, `job.finished`) plus agent events (`agent.connected`, `agent.disconnected`, `agent.lost`, `agent.stopping`) to dispatch the lightweight `queue-monitor.yml` workflow. This keeps queue counts and zombie-job alerts fresher without forcing the heavier CI collectors to run on every queue change.

Queue analytics intentionally exclude waiting or running jobs older than 4 hours. Those jobs are treated as zombies, surfaced separately in `queue_jobs.json`, and tracked via `queue_zombie_watcher.py` so the main queue charts stay conservative.

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
